from typing import Annotated,NamedTuple,BinaryIO
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import datetime
from collections import defaultdict
from hashlib import sha1
import hmac
import struct
import zipfile
import shutil
from tempfile import TemporaryDirectory
from enum import Enum


_FAR4_SAVE_KEY_LENGTH = 0x84
_FAR4_TABLE_ENTRY_LENGTH = 0x1c

LBP_FILE_EXTENSIONS = defaultdict(lambda: '',{b'BPRb':'.bpr',b'PLNb':'.plan',b'GMTb':'.gmat',b'LVLb':'.bin',b'SLTb':'.slt', b'MSHb':'.mol', b'TEX\x20':'.tex'})

class LbpMapRevision(Enum):
    LBP_VITA = b'\x00\x00\x03\xA8'
    LBP_PS3 = b'\x00\x00\x01\x00'
    LBP3 = b'\x01\x48\x01\x00'


class LbpMapEntry(NamedTuple):
    file_path: Path | None
    timestamp: datetime.datetime
    size: int
    hash: bytes
    guid: int


@dataclass(slots=True, frozen=True)
class LbpMapFile:
    revision: LbpMapRevision
    files: list[LbpMapEntry]

    @classmethod
    def from_map_file(cls, map_file: BinaryIO,/) -> 'LbpMapFile':
        revision_bytes = map_file.read(4)
        try:
            revision = LbpMapRevision(revision_bytes)
        except ValueError:
            map_file.seek(-4,1)
            raise ValueError(f'Unsupported maps file {revision_bytes.hex(" ")}')

        if revision in (LbpMapRevision.LBP_VITA,LbpMapRevision.LBP_PS3,LbpMapRevision.LBP3):
            file_count = int.from_bytes(map_file.read(4),'big',signed=True)
            files = []
            for _ in range(file_count):
                if revision in (LbpMapRevision.LBP_VITA,LbpMapRevision.LBP_PS3):
                    file_path_lenght = int.from_bytes(map_file.read(4),'big',signed=True)
                elif revision in (LbpMapRevision.LBP3,):
                    file_path_lenght = int.from_bytes(map_file.read(2),'big',signed=False)
                else:
                    raise AssertionError
                raw_path = map_file.read(file_path_lenght)
                if not raw_path:
                    file_path = None
                else:
                    file_path = Path(raw_path.decode())
                
                if revision in (LbpMapRevision.LBP_VITA,LbpMapRevision.LBP_PS3):
                    timestamp = datetime.datetime.fromtimestamp(int.from_bytes(map_file.read(8),'big',signed=True),tz=datetime.UTC)
                elif revision in (LbpMapRevision.LBP3,):
                    timestamp = datetime.datetime.fromtimestamp(int.from_bytes(map_file.read(4),'big',signed=False),tz=datetime.UTC)
                else:
                    raise AssertionError
                size = int.from_bytes(map_file.read(4),'big',signed=True)
                hash = map_file.read(0x14)
                guid = int.from_bytes(map_file.read(4),'big',signed=True)
                
                files.append(LbpMapEntry(file_path=file_path, timestamp=timestamp, size=size, hash=hash, guid=guid))
        else:
            raise AssertionError

        return cls(revision=revision, files=files)

    def export_to_file(self, out_file: BinaryIO,/) -> None:
        out_file.seek(0, 2)
        if out_file.tell():
            raise ValueError('Please give an empty file to as out_file')
        out_file.write(self.revision.value)
        
        if self.revision in (LbpMapRevision.LBP_VITA,LbpMapRevision.LBP_PS3,LbpMapRevision.LBP3):
            out_file.write(len(self.files).to_bytes(4,'big',signed=True))
            for file in self.files:
                ready_path = file.file_path.as_posix().encode() if file.file_path else b''
                if self.revision in (LbpMapRevision.LBP_VITA,LbpMapRevision.LBP_PS3):
                    file_path_lenght_bytes = len(ready_path).to_bytes(4,'big',signed=True)
                elif self.revision in (LbpMapRevision.LBP3,):
                    file_path_lenght_bytes = len(ready_path).to_bytes(2,'big',signed=True)
                else:
                    raise AssertionError
                out_file.write(file_path_lenght_bytes)
                out_file.write(ready_path)
                
                if self.revision in (LbpMapRevision.LBP_VITA,LbpMapRevision.LBP_PS3):
                    timestamp_bytes = int(file.timestamp.timestamp()).to_bytes(8,'big',signed=True)
                elif self.revision in (LbpMapRevision.LBP3,):
                    timestamp_bytes = int(file.timestamp.timestamp()).to_bytes(4,'big',signed=False)
                else:
                    raise AssertionError
                out_file.write(timestamp_bytes)
                out_file.write(file.size.to_bytes(4,'big',signed=True))
                out_file.write(file.hash)
                out_file.write(file.guid.to_bytes(4,'big',signed=True))
        else:
            raise AssertionError


@dataclass(slots=True)
class _FAR4TableEntry:
    filename: Path
    sha1: Annotated[bytes,0x14]
    offset: int
    length: int
    
    def __bytes__(self):
        return self.sha1 + struct.pack('>I',self.offset) + struct.pack('>I',self.length)


class _FAR4TableOffset(NamedTuple):
    table_offset: int
    file_count: int


def _get_far4_table_offset(far4_archive: BytesIO) -> _FAR4TableOffset:
    far4_archive.seek(-4,2)
    farc_revision = far4_archive.read(4)

    if farc_revision != b'FAR4':
        raise ValueError('Invalid far4 archive passed')
    
    far4_archive.seek(0)
    far4_archive.seek(-8,2)
    farc_file_count = struct.unpack('>i',far4_archive.read(4))[0]
    far4_archive.seek(0,2)
    farc_size = far4_archive.tell()
    far4_archive.seek(0)
    
    return _FAR4TableOffset(table_offset = farc_size - _FAR4_TABLE_ENTRY_LENGTH - farc_file_count * _FAR4_TABLE_ENTRY_LENGTH, file_count = farc_file_count)

class SaveKey():
    __slots__ = ('_save_key_bytes')

    def __init__(self, far4_archive: BytesIO):   
        table_offset = _get_far4_table_offset(far4_archive).table_offset
        far4_archive.seek(table_offset-_FAR4_SAVE_KEY_LENGTH)
        
        self._save_key_bytes = bytearray(far4_archive.read(_FAR4_SAVE_KEY_LENGTH))

    def __repr__(self) -> str:
        return f'{type(self).__name__}.from_string({str(self)!r})'
    
    def __str__(self) -> str:
        return self._save_key_bytes.hex()
    
    def __bytes__(self) -> bytes:
        return bytes(self._save_key_bytes)
    
    @classmethod
    def from_string(cls,save_key_hex_string: str):
        my_instance = cls.__new__(cls)
        new_save_key = bytearray.fromhex(save_key_hex_string)
        
        if not len(new_save_key) == _FAR4_SAVE_KEY_LENGTH:
            raise ValueError('Invalid key passed')
        
        my_instance._save_key_bytes = new_save_key
        return my_instance
        
    
    @property
    def root_resource_hash(self) -> Annotated[bytes,0x14]:
        return self._save_key_bytes[0x48: 0x48 + 0x14]
    @root_resource_hash.setter
    def root_resource_hash(self, value: Annotated[bytes,0x14]):
        if not len(value) == 0x14:
            raise ValueError('Invalid sha1 hash passed')
        self._save_key_bytes[0x48: 0x48 + 0x14] = value
    
    def write_to_far4(self, far4_archive: BytesIO):
        table_offset = _get_far4_table_offset(far4_archive).table_offset
        far4_archive.seek(table_offset-_FAR4_SAVE_KEY_LENGTH)
        far4_archive.write(bytes(self))

    def swap_endianness(self):
        self._save_key_bytes[0:4] = self._save_key_bytes[0:4][::-1]
        self._save_key_bytes[4:4+4] = self._save_key_bytes[4:4+4][::-1]
        self._save_key_bytes[0x34:0x34+4] = self._save_key_bytes[0x34:0x34+4][::-1]
        self._save_key_bytes[0x38:0x38+4] = self._save_key_bytes[0x38:0x38+4][::-1]
    
    @property
    def is_ps4_endian(self) -> bool:
        return bool(self._save_key_bytes[0x38])
    
    def set_to_ps3_endianness(self):
        if self.is_ps4_endian:
            self.swap_endianness()

    def set_to_ps4_endianness(self):
        if not self.is_ps4_endian:
            self.swap_endianness()
    
    @is_ps4_endian.setter
    def is_ps4_endian(self,value: bool):
        if value:
            self.set_to_ps4_endianness()
        else:
            self.set_to_ps3_endianness()

    @property
    def is_lbp3_revision(self) -> bool:
        return self._save_key_bytes[:4] == b'\xF9\x03\x18\x02' or self._save_key_bytes[:4] == b'\x02\x18\x03\xF9'


def extract_far4(file_archive: Path, output_folder: Path,*,extract_with_filename_extensions: bool = True,verify_hashes: bool = True) -> SaveKey:
    with open(file_archive,'rb') as f:
        table_offset,file_count = _get_far4_table_offset(f)

        f.seek(table_offset)
        for _ in range(file_count):
            entry_sha1 = f.read(0x14)
            entry_offset = struct.unpack('>i',f.read(4))[0]
            entry_length = struct.unpack('>i',f.read(4))[0]
            next_pointer = f.tell()
            f.seek(entry_offset,0)
            header: str = LBP_FILE_EXTENSIONS[f.read(4)]
            if not extract_with_filename_extensions:
                header = ''
            f.seek(-4,1)
            data = f.read(entry_length)
            if verify_hashes:
                mm = sha1()
                mm.update(data)
                real_entry_sha1 = mm.digest()
                if real_entry_sha1 != entry_sha1:
                    raise ValueError(f'Extracted file at {hex(entry_offset)} should have hash {entry_sha1.hex()}, instead is {real_entry_sha1.hex()}')
                
            with open(Path(output_folder,entry_sha1.hex() + header),'wb') as output_file:
                output_file.write(data)
            f.seek(next_pointer)
        return SaveKey(f)


def pack_far4(input_files: Path, output_file_archive: Path, save_key: SaveKey | None = None, key_file_resource_hash: Annotated[bytes,0x14] | None = None):
    new_hash = hmac.new(b'*\xfd\xa3\xca\x86\x02\x19\xb3\xe6\x8a\xff\xcc\x82\xc7k\x8a\xfe\n\xd8\x13_`G[\xdf]7\xbcW\x1c\xb5\xe7\x96u\xd5(\xa2\xfa\x90\xed\xdf\xa3E\xb4\x1f\xf9\x1f%\xe7BE;+\xb5>\x16\xc9X\x19{\xe7\x18\xc0\x80',b'',sha1)
    
    if not save_key:
        save_key = SaveKey.from_string('00'*0x84)
        key_file_resource_hash = b'\x00'*0x14
    
    save_key.root_resource_hash = key_file_resource_hash
    open(output_file_archive,'wb').close()
    
    farc_tables = []

    

    for file_count, filename in enumerate((x for x in Path(input_files).rglob('*') if x.is_file())):
        with open(filename,'rb') as input_file:
            data = input_file.read()
            data_length = input_file.tell()
            mm = sha1()
            mm.update(data)
            data_sha1 = mm.digest()
        table_entry = _FAR4TableEntry(filename = filename,sha1 = data_sha1, offset = 0xFFFFFFFF + 2, length = data_length)
        farc_tables.append(table_entry)
    file_count += 1

    with open(output_file_archive,'ab') as f:
        farc_tables.sort(key = lambda e: e.sha1.hex())

        for index, farc_table in enumerate(farc_tables):
            farc_tables[index].offset = f.tell()
            f.write(farc_table.filename.read_bytes()) # yeah i read the file twice, so what?

        if pad_amnt := f.tell() % 4:
            f.write(b'\x00' * (4 - pad_amnt))
        f.write(bytes(save_key))

        for farc_table in farc_tables:
            f.write(bytes(farc_table))

        f.write(b'\x00' * 0x14 + struct.pack('>I',file_count) + b'FAR4')


    with open(output_file_archive,'rb') as f:
        while True:
            data = f.read(4096)
            if not data:
                break
            new_hash.update(data)
    
    with open(output_file_archive,'rb+') as f:
        f.seek(-(0x14 + 4 + 4),2)
        f.write(new_hash.digest())


def files_to_map_lbp3(folder_with_files: Path, output_map: BytesIO):
    LBP3_REVISION = b'\x01\x48\x01\x00'
    output_map.seek(0,2)
    if output_map.tell():
        raise ValueError('must give an empty file in write mode')
    files_count = 0
    for file in folder_with_files.rglob('*'):
        if file.is_dir(): continue
        files_count += 1
    output_map.write(LBP3_REVISION)
    output_map.write(struct.pack('>i',files_count))
    
    for i,file in enumerate(folder_with_files.rglob('*')):
        if file.is_dir(): continue
        file_stat = file.stat()
        
        pretty_path = file.relative_to(folder_with_files.parent).as_posix().encode('ascii')
        
        output_map.write(struct.pack('>h',len(pretty_path)))
        
        output_map.write(pretty_path)

        output_map.write(struct.pack('>i',int(file_stat.st_mtime)))
        
        output_map.write(struct.pack('>i',int(file_stat.st_size)))
        
        hash = sha1()
        hash.update(file.read_bytes())
        output_map.write(hash.digest())
        
        output_map.write(struct.pack('>i',i))


def pack_to_mod(folder_with_files_to_pack_to_mod: Path, output_mod: zipfile.ZipFile):

    with TemporaryDirectory() as tp:
        tp = Path(tp)
        with open(tp / 'data.map','wb') as f:
            files_to_map_lbp3(folder_with_files_to_pack_to_mod,f)


        pack_far4(folder_with_files_to_pack_to_mod,tp / Path('data.farc'))

        with open(tp / 'config.json','w') as f:
            f.write('{"ID":"sample","type":"pack","title":"Untitled Mod","version":"1.0","author":"Sackthing","description":"No description was provided."}')

        output_mod.write(tp / 'data.farc', arcname = 'data.farc')
        output_mod.write(tp / 'data.map', arcname = 'data.map')
        output_mod.write(tp / 'config.json', arcname = 'config.json')