from tempfile import TemporaryDirectory
import argparse
from pathlib import Path
import zipfile
from typing import NamedTuple, Callable
from collections.abc import Generator
from enum import StrEnum, Enum
import sys
from hashlib import sha1
import datetime
from dataclasses import dataclass

from PIL import Image
from lbptoolspy import json2lbpfile, lbpfile2json, image2tex

from map_tools import extract_far4, pack_far4, SaveKey, LbpMapFile, LbpMapEntry


if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
    _ROOT_DIR = Path(sys._MEIPASS)
else:
    _ROOT_DIR = Path(__file__).parent
_MGS_VRTRAINING_RED_EDGE_GMAT_GUID_NUM_STR = '67464'
class GameForGlowingSolidColourGmat(StrEnum):
    lbp3ps4 = (_ROOT_DIR / 'lbp3ps4_glowing_solid_colour_gmat.json').read_text(encoding='utf-8')
del _ROOT_DIR


class Rgb(NamedTuple):
    red: int
    green: int
    blue: int

    @classmethod
    def from_hex_colour_code(cls, colour_hex: str) -> 'Rgb':
        colour_hex = colour_hex.strip()
        if not colour_hex.startswith('#'):
            raise ValueError('Not a hex colour string')
        if len(colour_hex) != len('#RRGGBB'):
            raise ValueError(f'{colour_hex} is not a hex colour string, maybe you passsed a hex colour with transparency?')
        
        colour_hex = colour_hex[1:]
        
        return Rgb(int(colour_hex[0:2], 16),int(colour_hex[2:4], 16),int(colour_hex[4:6], 16))
    
    @property
    def hex_colour_code(self) -> str:
        return f'#{self.red:02x}{self.green:02x}{self.blue:02x}'


class _ResourceType(Enum):
    INVALID = (0, "unknown/")
    TEXTURE = (1, ".tex")
    MESH = (2, ".mol")
    PIXEL_SHADER = (3, ".fpo")
    VERTEX_SHADER = (4, ".vpo")
    ANIMATION = (5, ".anim")
    GUID_SUBSTITUTION = (6, ".gsub")
    GFX_MATERIAL = (7, ".gmat")
    SPU_ELF = (8, ".sbu")
    LEVEL = (9, ".bin")
    FILENAME = (10, ".txt")
    SCRIPT = (11, ".ff")
    SETTINGS_CHARACTER = (12, ".cha")
    FILE_OF_BYTES = (13, ".raw")
    SETTINGS_SOFT_PHYS = (14, ".sph")
    FONTFACE = (15, ".fnt")
    MATERIAL = (16, ".mat")
    DOWNLOADABLE_CONTENT = (17, ".dlc")
    EDITOR_SETTINGS = (18, ".edset")
    JOINT = (19, ".joint")
    GAME_CONSTANTS = (20, ".con")
    POPPET_SETTINGS = (21, ".pop")
    CACHED_LEVEL_DATA = (22, ".cld")
    SYNCED_PROFILE = (23, ".pro")
    BEVEL = (24, ".bev")
    GAME = (25, ".game")
    SETTINGS_NETWORK = (26, ".nws")
    PACKS = (27, ".pck")
    BIG_PROFILE = (28, ".bpr")
    SLOT_LIST = (29, ".slt")
    TRANSLATION = (30, ".trans")
    ADVENTURE_CREATE_PROFILE = (31, ".adc")
    LOCAL_PROFILE = (32, ".ipr")
    LIMITS_SETTINGS = (33, ".lmt")
    TUTORIALS = (34, ".tut")
    GUID_LIST = (35, ".glst")
    AUDIO_MATERIALS = (36, ".aum")
    SETTINGS_FLUID = (37, ".flu")
    PLAN = (38, ".plan")
    TEXTURE_LIST = (39, ".yuv")
    MUSIC_SETTINGS = (40, ".mus")
    MIXER_SETTINGS = (41, ".mix")
    REPLAY_CONFIG = (42, ".rep")
    PALETTE = (43, ".pal")
    STATIC_MESH = (44, ".smh")
    ANIMATED_TEXTURE = (45, ".atx")
    VOIP_RECORDING = (46, ".vop")
    PINS = (47, ".pin")
    INSTRUMENT = (48, ".rinst")
    SAMPLE = (49, ".smp")
    OUTFIT_LIST = (50, ".oft")
    PAINT_BRUSH = (51, ".pbr")
    THING_RECORDING = (52, ".rec")
    PAINTING = (53, ".ptg")
    QUEST = (54, ".qst")
    ANIMATION_BANK = (55, ".abnk")
    ANIMATION_SET = (56, ".aset")
    SKELETON_MAP = (57, ".smap")
    SKELETON_REGISTRY = (58, ".sreg")
    SKELETON_ANIM_STYLES = (59, ".sas")
    CROSSPLAY_VITA = (60, ".cpv")
    STREAMING_CHUNK = (61, ".chk")
    ADVENTURE_SHARED_DATA = (62, ".ads")
    ADVENTURE_PLAY_PROFILE = (63, ".adp")
    ANIMATION_MAP = (64, ".amap")
    CACHED_COSTUME_DATA = (65, ".ccd")
    DATA_LABELS = (66, ".dla")
    ADVENTURE_MAPS = (67, ".adm")

    @classmethod
    def from_number(cls, number) -> '_ResourceType':
        res = next((item for item in cls if item.value[0] == number), None)
        if not res:
            raise ValueError(f'{number} is not a valid {cls.__name__}')
        return res


@dataclass(frozen=True, slots=True, eq=False)
class _DependencyEntry:
    entry: bytes | int
    resource_type: _ResourceType

    def __eq__(self, other):
        if isinstance(other, bytes):
            return isinstance(self.entry, bytes) and self.entry == other
        if isinstance(other, _DependencyEntry):
            return self.entry == other.entry and self.resource_type == other.resource_type
        return NotImplemented

    def __hash__(self):
        if isinstance(self.entry, bytes):
            return hash(self.entry)
        return hash((self.entry, self.resource_type))


def _get_depdency_list(file: BinaryIO) -> Generator[_DependencyEntry, None, None]:
    file.seek(3)
    if file.read(1) != b'b':
        return
    
    file.seek(8)
    file.seek(int.from_bytes(file.read(4),'big'))

    count = int.from_bytes(file.read(4),'big')
    for _ in range(count):
        guid: None | int = None
        found_sha1: bytes | None = None
        guid_or_hash = file.read(1)[0]
        if guid_or_hash == 2:
            guid = int.from_bytes(file.read(4),'big')
        elif guid_or_hash == 1:
            found_sha1 = file.read(20)
        else:
            raise AssertionError(f'unknwon {guid_or_hash = }')
        
        resource_type = _ResourceType.from_number(int.from_bytes(file.read(4),'big'))
        if found_sha1:
            yield _DependencyEntry(found_sha1,resource_type)
        else:
            yield _DependencyEntry(guid,resource_type)


def insert_glowing_solid_colour_gmat_into_a_mod(mod_file_input: Path, solid_colour: Rgb, game:GameForGlowingSolidColourGmat, gmat_hash_to_replace: bytes | None, output_mod: Path,*,make_mod_file_pretty: bool = True, do_print_func: Callable[str,None] = print) -> None:
    """Replaces a gmat in a mod file with one that glows a solid colour
    
    Args:
        mod_file_input (Path): input mod file to install the glowing solid colour gmat too
        solid_colour (Rgb): the colour you want
        game (GameForGlowingSolidColourGmat): the game the mod file is for
        gmat_hash_to_replace (bytes | None): optional arg for if there are mutiple gmats in the mod_file_input, use this gmat, or if it is a texture, itll fine the gmat the texture is linked too, if bytes must be 0x14 bytes long
        output_mod (Path): output mod file with the new gmat
    """
    if gmat_hash_to_replace:
        if len(gmat_hash_to_replace) != 0x14:
            raise ValueError(f'{gmat_hash_to_replace} is not a valid sha1')
    with TemporaryDirectory() as str_tp:
        tp = Path(str_tp)
        tmp_json = tp / 'tmp.json'
        mod_dump_dir = tp / 'mod_dump'
        mod_results_dir = tp / 'mod_results'
        mod_dump_dir.mkdir()
        do_print_func('extracting mod to temp dir')
        with open(mod_file_input,'rb') as f:
            if f.read(4) in (b'MODb',b'MODt'):
                raise ValueError('mods directly from workbench (which are old style mods) are not supported, please open the mod in Craftworld Toolkit (any version after Jan 10, 2023) then save it in order to update it to new mod format')
        with zipfile.ZipFile(mod_file_input, 'r') as zip_ref:
            if zip_ref.getinfo('data.farc').file_size > 99_000_000:
                raise Exception('mod file too big')
            zip_ref.extract('data.farc',mod_results_dir)
            zip_ref.extract('data.map',mod_results_dir)
            zip_ref.extract('config.json',mod_results_dir)
        
        map_output_file = mod_results_dir / 'data.map'
        farc_output_file = mod_results_dir / 'data.farc'
        config_json_that_is_internal_to_toolkit_mods_but_like_noone_ever_uses_it_zero_need_for_it_to_exist_btw_it_just_bloats_mod_files_even_if_by_a_3_kb_and_makes_packing_mod_tools_that_is_not_toolkit_a_bit_more_complex = mod_results_dir / 'config.json'
        
        with open(map_output_file,'rb') as f:
            og_mod_map = LbpMapFile.from_map_file(f)
        
        extract_far4(farc_output_file,mod_dump_dir,extract_with_filename_extensions=False)
        
        files: dict[Path:frozenset[_DependencyEntry,...]] = {}
        
        do_print_func('gathering depenecys lists')
        for file in mod_dump_dir.iterdir():
            with file.open('rb') as f:
                files[file] = frozenset(_get_depdency_list(f))
        found_one_gmat = False
        if not gmat_hash_to_replace:
            for value in files.values():
                for value2 in value:
                    if isinstance(value2.entry,bytes) and value2.resource_type == _ResourceType.GFX_MATERIAL:
                        if found_one_gmat:
                            raise ValueError('mutiple gmat hashes were found, you must provide one for gmat_hash_to_replace, open the mod in toolkit and look for either the gmat or texture you want to replace')
                        gmat_hash_to_replace = value2.entry
                        found_one_gmat = True
            if not found_one_gmat:
                raise ValueError('no gmats were found, if they are guid refrence gmats, you need to switch them to a hash one, you can even just find the guid refrence and make it a fake hash that does not even exist')
        for value in files.values():
            if gmat_hash_to_replace in value:
                for value2 in value:
                    if value2 == gmat_hash_to_replace:
                        if value2.resource_type == _ResourceType.TEXTURE:
                            for file,values3 in files.items():
                                if gmat_hash_to_replace in values3:
                                    with open(file,'rb') as f:
                                        if f.read(4) == b'GMTb':
                                            break
                            else: # nobreak
                                raise ValueError(f"bytes.fromhex('{gmat_hash_to_replace.hex()}') was a texture resource, but no gmat was found that depeneds on this texture, please give the gmat hash instead")
                            gmat_hash_to_replace = bytes.fromhex(file.name)
                        elif value2.resource_type != _ResourceType.GFX_MATERIAL:
                            raise ValueError(f"bytes.fromhex('{gmat_hash_to_replace.hex()}') needs to be a texture or gmat, not {value2.resource_type}")
        new_gmat = mod_dump_dir / gmat_hash_to_replace.hex()
        do_print_func('making new glowing texture')
        texture_data = image2tex(Image.new("RGB", (128, 256), color=solid_colour))
        gmat_texture_sha1 = sha1(texture_data).digest()
        gmat_texture_name = Path(f'{gmat_texture_sha1.hex()}.tex')
        (mod_dump_dir / gmat_texture_sha1.hex()).write_bytes(texture_data)
        og_mod_map.files.append(LbpMapEntry(gmat_texture_name,datetime.datetime.now(),len(texture_data),gmat_texture_sha1,max(x.guid for x in og_mod_map.files)+1))
        hash_to_entry_og_mod_map = {x.hash.hex():x for x in og_mod_map.files}

        do_print_func('making new glowing gmat')
        json_data = game.value.replace(_MGS_VRTRAINING_RED_EDGE_GMAT_GUID_NUM_STR,f'"{gmat_texture_sha1.hex()}"')
        gmat_sha1 = sha1(json2lbpfile(json_data,new_gmat)).hexdigest()
        do_print_func('fixing tables')
        orig_hash_to_new_hash: dict[bytes,bytes] = {}
        fixed_something = False
        def _fix_modified_file(orig_file: Path, new_hash: str) -> None:
            orig_hash_to_new_hash[bytes.fromhex(orig_file.name)] = bytes.fromhex(new_hash)
            for file,value in files.items():
                if bytes.fromhex(orig_file.name) in value:
                    do_print_func(f'Edited: {hash_to_entry_og_mod_map[file.name].file_path}')
                    lbpfile2json(file,tmp_json)
                    lbp_file_data_sha1 = sha1(json2lbpfile(tmp_json.read_text(encoding='utf-8').replace(f'"{orig_file.name}"',f'"{new_hash}"'),file)).hexdigest()
                    nonlocal fixed_something 
                    fixed_something = True
                    _fix_modified_file(file,lbp_file_data_sha1)
        _fix_modified_file(new_gmat,gmat_sha1)
        if not fixed_something:
            raise ValueError('either the gmat sha1 given was wrong, or there was no gmats in the mod file')
        do_print_func('inserting new gmat into map')
        gmat_sha1_name = Path(tp / 'this dir should never exist')
        for i,entry in enumerate(og_mod_map.files):
            new_hash = orig_hash_to_new_hash.get(entry.hash)
            if new_hash:
                if make_mod_file_pretty:
                    if entry.hash == gmat_hash_to_replace:
                        gmat_sha1_name = entry.file_path
                        gmat_sha1_name_sha1 = new_hash
                        gmat_sha1_name_size = (mod_dump_dir / gmat_hash_to_replace.hex()).stat().st_size
                og_mod_map.files[i] = entry._replace(hash=new_hash)
                del orig_hash_to_new_hash[entry.hash]
        
        for new_file_made_og_hash,new_file_made_new_hash in orig_hash_to_new_hash.items():
            if make_mod_file_pretty:
                gmat_sha1_name = Path(new_file_made_og_hash.hex() + '.gmat')
                gmat_sha1_name_sha1 = new_file_made_new_hash
                gmat_sha1_name_size = (mod_dump_dir / new_file_made_og_hash.hex()).stat().st_size
            og_mod_map.files.append(LbpMapEntry(Path(new_file_made_og_hash.hex() + '.gmat'),datetime.datetime.now(),Path(mod_dump_dir / new_file_made_og_hash.hex()).stat().st_size,new_file_made_new_hash,max(x.guid for x in og_mod_map.files)+1))
        
        if make_mod_file_pretty:
            index_to_delete = []
            for i,entry in enumerate(og_mod_map.files):
                if entry.file_path in (gmat_sha1_name,gmat_texture_name):
                    index_to_delete.append(i)
            for i in sorted(index_to_delete, reverse=True):
                del og_mod_map.files[i]
            game_ver = game.name
            def _func_so_i_can_return() -> tuple[Path,Path]:
                # loose files on root
                map_files = [x.file_path for x in og_mod_map.files]
                for file in map_files:
                    if len(file.parts) == 1:
                        return Path(f'glowing_solid_colour_gmat_mod_inserter_{game_ver}.gmat'),Path(f'glowing_solid_colour_gmat_mod_inserter_{game_ver}.tex')
                        
                common = []
                for parts in zip(*[x.parts for x in map_files]):
                    if len(set(parts)) == 1:
                        common.append(parts[0])
                    else:
                        break
                if not common:
                    # no loose files on root, no common dir, means there is mutiple top level dirs, make our own
                    return Path(f'glowing_solid_colour_gmat_mod_inserter/glowing_solid_colour_gmat_mod_inserter_{game_ver}.gmat'),Path(f'glowing_solid_colour_gmat_mod_inserter/glowing_solid_colour_gmat_mod_inserter_{game_ver}.tex')
                
                top_most_dir = Path(*common)
                for file in map_files:
                    if len(file.parts) - len(common) not in (1,0):
                        # more folders found in common dir, make folder in the top_most_dir
                        return Path(top_most_dir / f'glowing_solid_colour_gmat_mod_inserter/glowing_solid_colour_gmat_mod_inserter_{game_ver}.gmat'),Path(top_most_dir / f'glowing_solid_colour_gmat_mod_inserter/glowing_solid_colour_gmat_mod_inserter_{game_ver}.tex')
                        break
                else: # no break
                    # only loose files in the common dir, so dont make our own folder
                    return Path(top_most_dir / f'glowing_solid_colour_gmat_mod_inserter_{game_ver}.gmat'),Path(top_most_dir / f'glowing_solid_colour_gmat_mod_inserter_{game_ver}.tex')
            gmat_sha1_name,gmat_texture_name = _func_so_i_can_return()
            og_mod_map.files.append(LbpMapEntry(gmat_sha1_name,datetime.datetime.now(),gmat_sha1_name_size,gmat_sha1_name_sha1,max(x.guid for x in og_mod_map.files)+1))
            og_mod_map.files.append(LbpMapEntry(gmat_texture_name,datetime.datetime.now(),len(texture_data),gmat_texture_sha1,max(x.guid for x in og_mod_map.files)+1))
        do_print_func('building final mod')
        map_output_file.unlink()
        farc_output_file.unlink()
        with open(map_output_file,'wb') as f:
            og_mod_map.export_to_file(f)
        pack_far4(mod_dump_dir,farc_output_file)
        
        with zipfile.ZipFile(output_mod,'w') as zz:
            zz.write(map_output_file, arcname = 'data.map')
            zz.write(farc_output_file, arcname = 'data.farc')
            zz.write(config_json_that_is_internal_to_toolkit_mods_but_like_noone_ever_uses_it_zero_need_for_it_to_exist_btw_it_just_bloats_mod_files_even_if_by_a_3_kb_and_makes_packing_mod_tools_that_is_not_toolkit_a_bit_more_complex, arcname = 'config.json')
        do_print_func('done')


def main() -> None:
    """
    argparse ai gen
    """
    parser = argparse.ArgumentParser(
        prog="glowing_solid_colour_gmat_mod_inserter",
        description="Insert a glowing solid colour gmat into a mod file.",
    )

    parser.add_argument(
        "mod_file_input",
        type=Path,
        help="Path to the input mod file.",
    )

    parser.add_argument(
        "solid_colour",
        type=Rgb.from_hex_colour_code,
        metavar="HEX_COLOUR",
        help="Solid colour as a hex colour code, e.g. #123456.",
    )

    parser.add_argument(
        "game",
        choices=[game.name for game in GameForGlowingSolidColourGmat],
        metavar="GAME",
        help=(
            "The game the mod file is for. "
            f"such as: {', '.join(game.name for game in GameForGlowingSolidColourGmat)}"
        ),
    )

    parser.add_argument(
        "output_mod",
        type=Path,
        help="Path to the output mod file.",
    )

    parser.add_argument(
        "--gmat-hash-to-replace",
        type=_parse_gmat_hash,
        default=None,
        metavar="HASH",
        help="Optional 20-byte gmat hash in hexadecimal (40 hex characters).",
    )

    args = parser.parse_args()

    insert_glowing_solid_colour_gmat_into_a_mod(
        mod_file_input=args.mod_file_input,
        solid_colour=args.solid_colour,
        game=GameForGlowingSolidColourGmat[args.game],
        gmat_hash_to_replace=args.gmat_hash_to_replace,
        output_mod=args.output_mod,
    )


def _parse_gmat_hash(value: str) -> bytes:
    """Parse a gmat hash supplied as 40 hexadecimal characters."""
    try:
        result = bytes.fromhex(value.removeprefix("0x"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "gmat hash must be a valid hexadecimal string."
        ) from exc

    if len(result) != 0x14:
        raise argparse.ArgumentTypeError(
            "gmat hash must be exactly 20 bytes (40 hex characters)."
        )

    return result


if __name__ == "__main__":
    main()
