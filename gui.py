"""
file ai gen
"""
from __future__ import annotations

import queue
import re
import threading
import traceback
import tkinter as tk
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox, ttk
import sys

# ---------------------------------------------------------------------------
# Adjust this import to wherever your project actually lives.
# ---------------------------------------------------------------------------
from main import (
    GameForGlowingSolidColourGmat,
    Rgb,
    insert_glowing_solid_colour_gmat_into_a_mod,
)

_HEX_COLOUR_RE = re.compile(r"^#?[0-9a-fA-F]{6}$")


def parse_gmat_hash(value: str) -> bytes | None:
    """Same rules as the CLI's ``--gmat-hash-to-replace`` argument."""
    text = value.strip()
    if not text:
        return None

    text = text.removeprefix("0x").removeprefix("0X")

    try:
        result = bytes.fromhex(text)
    except ValueError as exc:
        raise ValueError(
            "gmat hash must be a valid hexadecimal string."
        ) from exc

    if len(result) != 0x14:
        raise ValueError(
            "gmat hash must be exactly 20 bytes (40 hex characters)."
        )

    return result


class GlowingSolidColourGmatGui(tk.Tk):
    POLL_INTERVAL_MS = 100

    GAMES = [game.name for game in GameForGlowingSolidColourGmat]

    def __init__(self) -> None:
        super().__init__()

        self.title("Glowing Solid Colour .mod gmat Inserter")
        self.minsize(780, 600)
        
        if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
            self.iconbitmap(str(Path(sys._MEIPASS) / "icon.ico"))
        else:
            self.iconbitmap(str(Path(__file__).parent / "icon.ico"))
        
        self._queue: queue.Queue[tuple[str, str, str | None]] = queue.Queue()
        self._running = False

        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.colour_var = tk.StringVar(value="#ffffff")
        self.game_var = tk.StringVar(value=self.GAMES[0] if self.GAMES else "")
        self.hash_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Ready.")

        self._build_ui()

        self.colour_var.trace_add("write", lambda *_: self._update_swatch())
        # Keep the gmat hash displayed in lowercase at all times.
        self.hash_var.trace_add("write", self._lowercase_hash)

        self._update_swatch()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(self.POLL_INTERVAL_MS, self._poll_queue)

    # ------------------------------------------------------------------ UI --

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        outer = ttk.Frame(self, padding=12)
        outer.grid(row=0, column=0, sticky="nsew")
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(6, weight=1)

        # --- input mod file ------------------------------------------------
        ttk.Label(outer, text="Input mod file:").grid(
            row=0, column=0, sticky="w", pady=4
        )
        ttk.Entry(outer, textvariable=self.input_var).grid(
            row=0, column=1, sticky="ew", padx=8, pady=4
        )
        ttk.Button(outer, text="Browse…", command=self._browse_input).grid(
            row=0, column=2, sticky="ew", pady=4
        )

        # --- output mod file -----------------------------------------------
        ttk.Label(outer, text="Output mod file:").grid(
            row=1, column=0, sticky="w", pady=4
        )
        ttk.Entry(outer, textvariable=self.output_var).grid(
            row=1, column=1, sticky="ew", padx=8, pady=4
        )
        ttk.Button(outer, text="Save as…", command=self._browse_output).grid(
            row=1, column=2, sticky="ew", pady=4
        )

        # --- solid colour ---------------------------------------------------
        ttk.Label(outer, text="Solid colour:").grid(
            row=2, column=0, sticky="w", pady=4
        )

        colour_frame = ttk.Frame(outer)
        colour_frame.grid(row=2, column=1, sticky="ew", padx=8, pady=4)
        colour_frame.columnconfigure(0, weight=1)

        ttk.Entry(colour_frame, textvariable=self.colour_var).grid(
            row=0, column=0, sticky="ew"
        )

        self.swatch = tk.Canvas(
            colour_frame,
            width=56,
            height=24,
            highlightthickness=1,
            highlightbackground="#7a7a7a",
            bd=0,
        )
        self.swatch.grid(row=0, column=1, padx=(8, 0))
        self._swatch_rect = self.swatch.create_rectangle(
            0, 0, 56, 24, fill="#ffffff", outline=""
        )

        ttk.Button(outer, text="Pick colour…", command=self._pick_colour).grid(
            row=2, column=2, sticky="ew", pady=4
        )

        # --- game -----------------------------------------------------------
        ttk.Label(outer, text="Game:").grid(
            row=3, column=0, sticky="w", pady=4
        )
        game_box = ttk.Combobox(
            outer,
            textvariable=self.game_var,
            values=self.GAMES,
            state="readonly",
        )
        game_box.grid(row=3, column=1, sticky="ew", padx=8, pady=4)
        ttk.Label(outer, text="").grid(row=3, column=2)

        # --- gmat hash ------------------------------------------------------
        ttk.Label(outer, text="gmat hash to replace:").grid(
            row=4, column=0, sticky="w", pady=4
        )

        hash_frame = ttk.Frame(outer)
        hash_frame.grid(row=4, column=1, sticky="ew", padx=8, pady=4)
        hash_frame.columnconfigure(0, weight=1)

        ttk.Entry(hash_frame, textvariable=self.hash_var).grid(
            row=0, column=0, sticky="ew"
        )
        ttk.Label(
            hash_frame,
            text="Optional — 40 hex characters (20 bytes). Leave blank to auto-detect.",
            foreground="#666666",
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))
        ttk.Label(outer, text="").grid(row=4, column=2)

        # --- action buttons --------------------------------------------------
        button_frame = ttk.Frame(outer)
        button_frame.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(12, 6))

        self.insert_button = ttk.Button(
            button_frame, text="Insert gmat", command=self._on_insert
        )
        self.insert_button.pack(side="left")

        ttk.Button(
            button_frame, text="Clear log", command=self._clear_log
        ).pack(side="right")

        # --- progress log ----------------------------------------------------
        log_frame = ttk.LabelFrame(outer, text="Progress", padding=6)
        log_frame.grid(row=6, column=0, columnspan=3, sticky="nsew", pady=4)
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)

        self.log_text = tk.Text(
            log_frame,
            height=14,
            wrap="word",
            state="disabled",
            background="#1e1e1e",
            foreground="#d4d4d4",
            insertbackground="#d4d4d4",
            relief="flat",
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")

        scrollbar = ttk.Scrollbar(
            log_frame, orient="vertical", command=self.log_text.yview
        )
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)
        self.log_text.tag_configure("error", foreground="#ff6b6b")

        # --- status bar -------------------------------------------------------
        ttk.Label(
            outer, textvariable=self.status_var, anchor="w", foreground="#444444"
        ).grid(row=7, column=0, columnspan=3, sticky="ew", pady=(6, 0))

    # ------------------------------------------------------- colour helpers --

    def _update_swatch(self) -> None:
        text = self.colour_var.get().strip()
        if _HEX_COLOUR_RE.match(text):
            normalised = "#" + text.lstrip("#")
            self.swatch.itemconfigure(self._swatch_rect, fill=normalised)
            self.swatch.configure(highlightbackground="#7a7a7a")
        else:
            self.swatch.itemconfigure(self._swatch_rect, fill="#3a3a3a")
            self.swatch.configure(highlightbackground="#b00020")

    def _pick_colour(self) -> None:
        current = self.colour_var.get().strip()
        initial = "#" + current.lstrip("#") if _HEX_COLOUR_RE.match(current) else "#ffffff"

        _, hex_colour = colorchooser.askcolor(
            color=initial,
            title="Choose a solid colour",
            parent=self,
        )

        if hex_colour:
            self.colour_var.set(hex_colour.lower())

    def _lowercase_hash(self, *_: object) -> None:
        value = self.hash_var.get()
        lowered = value.lower()
        if value != lowered:
            self.hash_var.set(lowered)

    # -------------------------------------------------------- file browsers --

    def _browse_input(self) -> None:
        path = filedialog.askopenfilename(
            parent=self, title="Select input mod file",
            defaultextension=".mod",
        filetypes=[("Mod files", "*.mod"), ("All files", "*.*")],
        )
        if not path:
            return

        self.input_var.set(path)



    def _browse_output(self) -> None:
        if self.input_var.get():
            source = Path(self.input_var.get())
        else:
            source = Path("mod.mod")
        path = filedialog.asksaveasfilename(
            parent=self, title="Select output mod file",
            defaultextension=".mod",
        filetypes=[("Mod files", "*.mod"), ("All files", "*.*")],
        initialfile=source.with_name(f"{source.stem}_glowing{source.suffix}").name
        )
        if path:
            self.output_var.set(path)

    # ---------------------------------------------------------- validation --

    def _collect_params(self) -> dict[str, object]:
        input_text = self.input_var.get().strip()
        if not input_text:
            raise ValueError("Please choose an input mod file.")

        input_path = Path(input_text)
        if not input_path.is_file():
            raise ValueError(f"Input mod file does not exist:\n{input_path}")

        output_text = self.output_var.get().strip()
        if not output_text:
            raise ValueError("Please choose an output mod file.")

        colour_text = self.colour_var.get().strip()
        if not colour_text:
            raise ValueError("Please choose a solid colour.")

        try:
            solid_colour = Rgb.from_hex_colour_code(colour_text)
        except Exception as exc:
            raise ValueError(f"Invalid colour {colour_text!r}: {exc}") from exc

        game_name = self.game_var.get().strip()
        if not game_name:
            raise ValueError("Please choose a game.")

        try:
            game = GameForGlowingSolidColourGmat[game_name]
        except KeyError as exc:
            raise ValueError(f"Unknown game {game_name!r}.") from exc

        try:
            gmat_hash = parse_gmat_hash(self.hash_var.get())
        except ValueError as exc:
            raise ValueError(str(exc)) from exc

        return {
            "mod_file_input": input_path,
            "solid_colour": solid_colour,
            "game": game,
            "gmat_hash_to_replace": gmat_hash,
            "output_mod": Path(output_text),
        }

    # ------------------------------------------------------------- running --

    def _on_insert(self) -> None:
        if self._running:
            return

        try:
            params = self._collect_params()
        except ValueError as exc:
            messagebox.showerror("Invalid input", str(exc), parent=self)
            return

        output_path: Path = params["output_mod"]  # type: ignore[assignment]
        if output_path.exists():
            if not messagebox.askyesno(
                "Overwrite output?",
                f"This file already exists and will be overwritten:\n\n{output_path}\n\nContinue?",
                icon="warning",
                parent=self,
            ):
                self.status_var.set("Cancelled — output file not overwritten.")
                return

        self._running = True
        self.insert_button.configure(state="disabled")
        self.status_var.set("Working…")

        self._clear_log()

        self._append_log("Starting…")
        ...
        self._append_log(f"  input : {params['mod_file_input']}")
        self._append_log(f"  output: {params['output_mod']}")
        self._append_log(f"  game  : {params['game'].name}")
        self._append_log(f"  colour: {self.colour_var.get().strip().lower()}")

        gmat_hash = params["gmat_hash_to_replace"]
        if isinstance(gmat_hash, bytes):
            # .hex() is already lowercase.
            self._append_log(f"  gmat  : {gmat_hash.hex()}")
        else:
            self._append_log("  gmat  : (auto-detect)")

        threading.Thread(
            target=self._worker, args=(params,), daemon=True
        ).start()

    def _worker(self, params: dict[str, object]) -> None:
        def do_print(*args: object, **_kwargs: object) -> None:
            self._queue.put(("log", " ".join(str(arg) for arg in args), None))

        try:
            insert_glowing_solid_colour_gmat_into_a_mod(
                mod_file_input=params["mod_file_input"],
                solid_colour=params["solid_colour"],
                game=params["game"],
                gmat_hash_to_replace=params["gmat_hash_to_replace"],
                output_mod=params["output_mod"],
                do_print_func=do_print,
            )
        except Exception as exc:
            summary = f"{exc}"
            detail = traceback.format_exc()
            self._queue.put(("error", summary, detail))
            self._queue.put(("done", "error", None))
        else:
            self._queue.put(("log", "Finished successfully.", None))
            self._queue.put(("done", "success", None))

    # --------------------------------------------------------------- queue --

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, payload, detail = self._queue.get_nowait()

                if kind == "log":
                    self._append_log(payload)
                elif kind == "error":
                    self._append_log(payload, tag="error")
                    if detail:
                        self._append_log(detail, tag="error")
                    messagebox.showerror(
                        "Insert failed",
                        f"{payload}\n\nSee the progress log for details.",
                        parent=self,
                    )
                elif kind == "done":
                    self._running = False
                    self.insert_button.configure(state="normal")
                    if payload == "success":
                        self.status_var.set("Done.")
                        messagebox.showinfo(
                            "Insert complete",
                            f"Glowing solid-colour gmat inserted successfully.\n\n"
                            f"Output: {self.output_var.get().strip()}",
                            parent=self,
                        )
                    else:
                        self.status_var.set("Failed — see the progress log.")
        except queue.Empty:
            pass

        self.after(self.POLL_INTERVAL_MS, self._poll_queue)

    # ----------------------------------------------------------------- log --

    def _append_log(self, message: str, tag: str | None = None) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message.rstrip() + "\n", tag)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    # -------------------------------------------------------------- closing --

    def _on_close(self) -> None:
        if self._running and not messagebox.askyesno(
            "Still running",
            "An insert is still in progress. Quit anyway?",
            parent=self,
        ):
            return
        self.destroy()


if __name__ == "__main__":
    GlowingSolidColourGmatGui().mainloop()