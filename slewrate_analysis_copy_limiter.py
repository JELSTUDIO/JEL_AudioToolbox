import numpy as np
import scipy.signal as signal
import soundfile as sf
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import json
import os
import threading
import time
import glob
import traceback as tb_module
from concurrent.futures import ThreadPoolExecutor, as_completed
import psutil
from numba import njit

# ─── helpers ────────────────────────────────────────────────────────────────────
# ─── Constants ────────────────────────────────────────────────────────────────
APP_VERSION = "v1.0.0"
GUARD_REFERENCE_FS = 96000.0  # Reference rate for sample-rate agnostic guard band mapping
SETTINGS_FILENAME = "analog_slew_app_settings.json"

@njit(cache=True)
def _limit_channel_numba(ch, max_delta, knee_fraction, prev_sample):
    n = len(ch)
    out = np.empty(n)
    last_out = prev_sample
    
    lo = (1.0 - knee_fraction) * max_delta if knee_fraction > 0.0 else 0.0
    span = max_delta - lo if knee_fraction > 0.0 else 0.0

    for i in range(n):
        diff = ch[i] - last_out
        abs_d = abs(diff)
        
        if knee_fraction <= 0.0:
            # Hard exponential clamp: strictly caps at max_delta, no passband attenuation
            if abs_d > max_delta:
                step = max_delta * (1.0 if diff >= 0 else -1.0)
            elif abs_d > max_delta * 0.85:
                # Smooth exponential knee only in the limiting zone
                t = (abs_d - max_delta * 0.85) / (max_delta * 0.15)
                k = 1.0 - np.exp(-4.0 * t)
                step = max_delta * k * (1.0 if diff >= 0 else -1.0)
            else:
                # Full passband: no attenuation, exact tracking
                step = diff
        else:
            # Smooth exponential knee for soft-knee mode
            if abs_d <= lo:
                step = diff
            elif abs_d < max_delta:
                t = (abs_d - lo) / span
                k = 1.0 - np.exp(-3.0 * t)
                val = lo + (max_delta - lo) * k
                step = (1.0 if diff >= 0 else -1.0) * val
            else:
                step = max_delta * (1.0 if diff >= 0 else -1.0)
        
        last_out += step
        out[i] = last_out
        
    return out

def _anti_alias_fir(fs_up: float, fs_out: float, n_taps: int, stopband_db: float, guard_band: float):
    # ──────────────────────────────────────────────────────────────────────
    # SAMPLE-RATE AGNOSTIC GUARD BAND MAPPING
    # ──────────────────────────────────────────────────────────────────────
    REFERENCE_FS = 96000.0  
    target_cutoff_hz = guard_band * (REFERENCE_FS / 2.0)
    
    nyq_out = fs_out / 2.0
    cutoff_hz = min(target_cutoff_hz, nyq_out * 0.995)
    
    beta = 0.1102 * (stopband_db - 8.7) + 0.44 * np.sqrt(stopband_db - 8.7)
    if n_taps % 2 == 0:
        n_taps += 1
    taps = signal.firwin(n_taps, cutoff_hz, window=("kaiser", beta), fs=fs_up)
    return taps

class ProgressTracker:
    def __init__(self, total_work, report_callback, global_sample_adder=None):
        self.total_work = total_work
        self.report_callback = report_callback
        self.completed_samples = 0
        self.lock = threading.Lock()
        self.last_report_time = time.time()
        self.global_sample_adder = global_sample_adder

    def add_progress(self, n_samples, step_id, msg):
        with self.lock:
            self.completed_samples += n_samples
        
        if self.global_sample_adder:
            self.global_sample_adder(n_samples)

        current_time = time.time()
        if (current_time - self.last_report_time > 0.03) or (self.completed_samples >= self.total_work):
            percent = min((self.completed_samples / self.total_work) * 100, 100.0)
            if self.report_callback:
                self.report_callback(step_id, percent, msg)
            self.last_report_time = current_time

class SlewEngine:
    def __init__(self):
        self.ref_data = None
        self.ref_metadata = {}

    def measure_slew_rate(self, file_path):
        with sf.SoundFile(file_path) as f:
            fs = f.samplerate
            channels = f.channels

            slew_abs = np.zeros(channels)
            peak_amps = np.zeros(channels)
            prev_sample = None  # State variable for block boundaries

            # Use integer sample count for blocks() to avoid float warnings
            block_size = int(fs * 4)  

            for block in f.blocks(blocksize=block_size, dtype='float64'):
                if block.ndim == 1:
                    block = block[:, np.newaxis]

                # 1. Handle boundary crossing from previous block
                if prev_sample is not None:
                    boundary_diff = np.abs(block[0, :] - prev_sample) * fs
                    slew_abs = np.maximum(slew_abs, boundary_diff)

                # 2. Compute internal sample-to-sample differences
                dy = np.abs(np.diff(block, axis=0)) * fs
                if dy.size > 0:  # Safety for single-sample blocks
                    slew_abs = np.maximum(slew_abs, np.max(dy, axis=0))

                # 3. Track peak amplitude for ratio calculation
                peak_amps = np.maximum(peak_amps, np.max(np.abs(block), axis=0))

                prev_sample = block[-1]  # Save last sample for next iteration

            if np.all(peak_amps == 0):
                return None, None, None

            slew_ratio = np.divide(slew_abs, peak_amps, out=np.zeros_like(peak_amps), where=peak_amps!=0)

            metadata = {
                "file_name": os.path.basename(file_path),
                "file_path": file_path,
                "sample_rate": fs,
                "channels": channels,
                "slew_ratio": slew_ratio.tolist(),
                "slew_abs": slew_abs.tolist(),
                "is_stereo": channels > 1,
            }
        return slew_ratio, metadata, fs

    def apply_slew_limit(self, target_path, slew_ratio_arr, slew_abs_arr, mode, apply_mode, oversample_factor, output_path, guard_band, ram_limit_gb, progress_callback=None, global_sample_adder=None, knee_fraction=0.35, stopband_db=96.0):
        def report(step_id, percent, msg):
            if progress_callback: progress_callback(step_id, percent, msg)

        with sf.SoundFile(target_path) as infile:
            fs = infile.samplerate
            num_channels = infile.channels
            original_subtype = infile.subtype
            total_frames = infile.frames
            
            s_ratio = np.array(slew_ratio_arr)
            s_abs = np.array(slew_abs_arr)

            # Determine target absolute slew rate per channel matching measurement definition
            if mode == "peak":
                report("upsampling", 0, "Scanning target peak amplitude...")
                peak_amps = np.zeros(num_channels)
                for block in infile.blocks(blocksize=fs*2, dtype='float64'):
                    if block.ndim == 1: block = block[:, np.newaxis]
                    peak_amps = np.maximum(peak_amps, np.max(np.abs(block), axis=0))
                report("upsampling", 100, "Scan complete.")
                target_slew_per_ch = s_ratio * peak_amps
            else:
                target_slew_per_ch = s_abs

            if apply_mode == "balanced":
                max_slew_val = np.max(target_slew_per_ch)
                target_slew_per_ch = np.full(num_channels, max_slew_val)

            # Apply exactly 1:1 to measurement math: slew_abs * dt_up = max allowed sample difference
            fs_up = fs * oversample_factor
            dt_up = 1.0 / fs_up
            
            n_taps = 2 * int(oversample_factor * 16) + 1
            taps = _anti_alias_fir(fs_up, fs, n_taps, stopband_db, guard_band)
            
            # Compensate for FIR filter ringing/overshoot that breaks the clamp post-limiting
            fir_energy = np.sum(np.abs(taps))
            SLEW_MATCH_FACTOR = 1.1  # >1.0 raises limit slightly; tune between 1.00–1.05
            max_deltas = (target_slew_per_ch * dt_up) / fir_energy * SLEW_MATCH_FACTOR
            
            zi = [np.zeros(len(taps) - 1) for _ in range(num_channels)]
            prev_limiter_sample = np.zeros(num_channels)
            first_block = True
            
            target_bytes = ram_limit_gb * (1024**3)
            safety_multiplier = 6.0 
            bytes_per_input_sample = (oversample_factor * num_channels * 8 * safety_multiplier)
            calculated_chunk_size = int(target_bytes / bytes_per_input_sample)
            chunk_size = max(1024, min(calculated_chunk_size, 50_000_000))

            with sf.SoundFile(output_path, mode='w', samplerate=fs, channels=num_channels, subtype=original_subtype) as outfile:
                infile.seek(0)

                tracker = ProgressTracker(total_frames, lambda s, p, m: report("limiting", p, m), global_sample_adder=global_sample_adder)

                report("limiting", 0, "Processing audio...")

                for block in infile.blocks(blocksize=chunk_size, dtype='float64'):
                    if first_block:
                        if block.ndim == 1:
                            prev_limiter_sample[:] = block[0]
                        else:
                            prev_limiter_sample[:] = block[0, :]
                        first_block = False
        
                    if block.ndim == 1:
                        block = block[:, np.newaxis]
                    
                    n_block = block.shape[0]
                    
                    # ─── UPSAMPLING & PADDING SETUP ─────────────────────────────────────
                    up_block = signal.resample_poly(block, oversample_factor, 1)
                    
                    # Exact linear-phase filter latency: (n_taps - 1) / 2 maps to exactly 16 original samples
                    delay_up = oversample_factor * 16
                    pad_len = delay_up
 
                    # Pre-allocate padded arrays to prevent boundary truncation during decimation
                    up_block_padded = np.zeros((up_block.shape[0] + pad_len, num_channels), dtype=np.float64)
                    up_block_padded[:up_block.shape[0], :] = up_block
 
                    processed_up_block = np.empty_like(up_block_padded)
                    sub_chunk_size = 50000 
 
                    for sub_start in range(0, up_block_padded.shape[0], sub_chunk_size):
                        sub_end = min(sub_start + sub_chunk_size, up_block_padded.shape[0])
                        sub_slice = up_block_padded[sub_start:sub_end, :]
                        sub_out = np.empty_like(sub_slice)
 
                        for ch in range(num_channels):
                            limited_ch = _limit_channel_numba(sub_slice[:, ch], max_deltas[ch], knee_fraction, prev_limiter_sample[ch])
                            prev_limiter_sample[ch] = limited_ch[-1]
                            filtered_ch, new_zi = signal.lfilter(taps, [1.0], limited_ch, zi=zi[ch])
                            sub_out[:, ch] = filtered_ch
                            zi[ch] = new_zi
 
                        processed_up_block[sub_start:sub_end, :] = sub_out
                        original_samples_in_sub = (sub_end - sub_start) // oversample_factor
                        if sub_start + (original_samples_in_sub * oversample_factor) < sub_end:
                            original_samples_in_sub += 1
                        tracker.add_progress(original_samples_in_sub, "limiting", "")
 
                    # ─── PHASE-COMPENSATED DECIMATION & EXACT LENGTH TRIMMING ──────────
                    final_chunk = processed_up_block[delay_up::oversample_factor, :][:n_block, :]
 
                    outfile.write(final_chunk)

        report("saving", 100, "Finished")

# ─── Safe Output Path Generator ────────────────────────────────────────────────
def get_unique_output_path(output_dir, original_stem, output_ext=".wav"):
    base = f"{original_stem}_slewed"
    candidate = os.path.join(output_dir, f"{base}{output_ext}")
    counter = 1
    while os.path.exists(candidate):
        candidate = os.path.join(output_dir, f"{base}_v{counter}{output_ext}")
        counter += 1
    return candidate

# ─── GUI ──────────────────────────────────────────────────────────────────────
class SlewGUI:
    def __init__(self, root):
        self.root = root
        self.root.title(f"Analog Slew-Rate Emulation Engine {APP_VERSION}")
        self.root.geometry("800x920")
        self.dark_mode = tk.BooleanVar(value=False)
        self.root.minsize(800, 920)  # Locks minimum window size to your layout dimensions
        self.engine = SlewEngine()
        self.current_preset = None
        
        self.process_mode = "single"  # "single" or "batch"
        self.target_files = []
        self.output_root = None

        self.setup_ui()
        self._load_settings()  # <-- Auto-load settings on startup
        self.apply_theme()
        self._set_log_theme()  # 👈 Guarantees initial colors are forced

    def _set_log_theme(self):
        """Forces the log window to update its background/text across theme toggles"""
        is_dark = self.dark_mode.get()
        bg = '#1e1e1e' if is_dark else '#ffffff'
        fg = '#e0e0e0' if is_dark else '#1a1a1a'
        sel_bg = '#2d2d30' if is_dark else '#4a90d9'
        
        self.status_listbox.config(
            bg=bg, 
            fg=fg, 
            selectbackground=sel_bg, 
            selectforeground='#ffffff',
            highlightthickness=0,  # 🔑 Key: removes native OS border that overrides bg
            bd=0                   # Removes extra padding
        )

    def apply_theme(self):
        style = ttk.Style()
        style.theme_use('clam')  # Cross-platform consistent base
        
        is_dark = self.dark_mode.get()
        if is_dark:
            bg, fg = '#1e1e1e', '#e0e0e0'
            field_bg, list_bg = '#2d2d2d', '#1e1e1e'
            btn_bg, btn_fg = '#3c3c3c', '#ffffff'
            border_color = '#555555'
        else:
            bg, fg = '#f0f0f0', '#1a1a1a'
            field_bg, list_bg = '#ffffff', '#ffffff'
            btn_bg, btn_fg = '#e8e8e8', '#000000'
            border_color = '#888888'

        # Configure all ttk widgets
        style.configure('.', background=bg, foreground=fg, fieldbackground=field_bg)
        style.configure('TButton', background=btn_bg, foreground=btn_fg, bordercolor=border_color, darkcolor='#777777')
        style.configure('TRadiobutton', background=bg, indicatorcolor=bg, foreground=fg)
        style.configure('TLabel', background=bg, foreground=fg)
        style.configure('TProgressbar', background='#4caf50', troughcolor=field_bg)

        # Apply to root and recursively color native widgets
        self.root.config(background=bg)
        # FIX: Correct method name + pass all required arguments
        self._set_widget_colors(self.root, bg, fg, list_bg, field_bg)
        self._set_log_theme()  # 👈 Force log window update

    def _set_log_theme(self):
        """Forces the processing log to respect dark/light colors"""
        is_dark = self.dark_mode.get()
        bg = '#1e1e1e' if is_dark else '#ffffff'
        fg = '#e0e0e0' if is_dark else '#1a1a1a'
        sel_bg = '#2d2d30' if is_dark else '#4a90d9'
        
        self.status_listbox.config(
            bg=bg, 
            fg=fg, 
            selectbackground=sel_bg, 
            selectforeground='#ffffff',
            highlightthickness=0,  # Removes native OS border that overrides background
            bd=0                   # Removes default padding
        )

    def _set_widget_colors(self, widget, bg, fg, list_bg, field_bg):
        try:
            if isinstance(widget, (tk.Entry, tk.Text)):
                widget.config(bg=field_bg, fg=fg, insertbackground=fg)
            elif isinstance(widget, tk.Listbox):
                sel_bg = '#3c3c3c' if self.dark_mode.get() else '#4a90d9'
                widget.config(bg=list_bg, fg=fg, selectbackground=sel_bg, selectforeground='#ffffff')
            elif isinstance(widget, (ttk.Frame, tk.Frame)):
                widget.config(background=bg)

            for child in widget.winfo_children():
                self._set_widget_colors(child, bg, fg, list_bg, field_bg)
        except:  # Ignore destroyed widgets during configuration
            pass

    def toggle_dark_mode(self):
        self.apply_theme()
        self._set_log_theme()  # 👈 Ensure log updates instantly on toggle

    def setup_ui(self):
        # Updated padding to be more compact vertically
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # RAM Limit Slider
        ttk.Label(main_frame, text="Max RAM Usage per Worker (GB):").pack(anchor=tk.W, pady=(0, 0))
        self.ram_val = tk.DoubleVar(value=8.0)
        self.slider_ram = ttk.Scale(main_frame, from_=0.1, to=16.0, variable=self.ram_val, orient=tk.HORIZONTAL)
        self.slider_ram.pack(fill=tk.X)
        
        self.lbl_ram_val = ttk.Label(main_frame, text="8.0 GB")
        self.lbl_ram_val.pack(anchor=tk.E)
        ttk.Label(main_frame, text="(Applies to each batch process)", foreground="gray", font=("Arial", 8)).pack(anchor=tk.E)
        self.slider_ram.configure(command=self.update_ram_label)

        # Step 1: Preset Management
        ttk.Label(main_frame, text="Step 1: Preset Management", font=('Helvetica', 10, 'bold')).pack(anchor=tk.W, pady=(0, 0))
        
        # NEW: Side-by-side buttons for Reference Analysis and Load Preset
        preset_btns_frame = ttk.Frame(main_frame)
        preset_btns_frame.pack(fill=tk.X, pady=0)
        self.btn_load_ref = ttk.Button(preset_btns_frame, text="Analyze New Reference Audio", command=self.load_reference)
        self.btn_load_ref.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 2))
        
        self.btn_load_existing = ttk.Button(preset_btns_frame, text="Load Existing Preset (.json)", command=self.load_existing_preset)
        self.btn_load_existing.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0))
        
        self.lbl_ref_status = ttk.Label(main_frame, text="No preset loaded", foreground="gray")
        self.lbl_ref_status.pack(anchor=tk.W, pady=0)
        self.btn_save_preset = ttk.Button(main_frame, text="Save Current Analysis as Preset", command=self.save_preset, state=tk.DISABLED)
        self.btn_save_preset.pack(fill=tk.X, pady=0)

        # === NEW: Application Settings Persistence ===
        ttk.Separator(main_frame, orient='horizontal').pack(fill=tk.X, pady=2)
        ttk.Label(main_frame, text="Application Configuration", font=('Helvetica', 10, 'bold')).pack(anchor=tk.W, pady=(0, 0))
        
        # 👇 Dark mode toggle
        chk_dark = ttk.Checkbutton(main_frame, text="Enable Dark Mode", variable=self.dark_mode, command=self.toggle_dark_mode)
        chk_dark.pack(anchor=tk.W, pady=(0, 5))
        
        self.btn_save_settings = ttk.Button(main_frame, text="Save Current Settings to Disk", command=self._save_settings)
        self.btn_save_settings.pack(fill=tk.X, pady=0)
        ttk.Label(main_frame, text="(Restored automatically on next startup)", foreground="gray", font=("Arial", 8)).pack(anchor=tk.E)
        # ===========================================

        ttk.Separator(main_frame, orient='horizontal').pack(fill=tk.X, pady=2)

        # Step 2: Target Processing
        ttk.Label(main_frame, text="Step 2: Target Processing", font=('Helvetica', 10, 'bold')).pack(anchor=tk.W, pady=(0, 0))
        
        mode_frame = ttk.LabelFrame(main_frame, text="Processing Mode", padding="8")
        mode_frame.pack(fill=tk.X, pady=0)
        self.mode_var = tk.StringVar(value="single")
        ttk.Radiobutton(mode_frame, text="Single File", variable=self.mode_var, value="single").pack(anchor=tk.W)
        ttk.Radiobutton(mode_frame, text="Folder / Batch (All files)", variable=self.mode_var, value="batch").pack(anchor=tk.W)

        # NEW: Ensure Select Targets and Set Output are compact side-by-side
        tgt_frame = ttk.Frame(main_frame)
        tgt_frame.pack(fill=tk.X, pady=0)
        self.btn_load_target = ttk.Button(tgt_frame, text="Select Target(s)", command=self.select_targets)
        self.btn_load_target.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 3))
        self.lbl_target_info = ttk.Label(tgt_frame, text="No targets selected", foreground="gray")
        self.lbl_target_info.pack(side=tk.RIGHT, anchor=tk.E)

        out_frame = ttk.Frame(main_frame)
        out_frame.pack(fill=tk.X, pady=0)
        self.btn_set_output = ttk.Button(out_frame, text="Set Output Directory", command=self.select_output_dir)
        self.btn_set_output.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 3))
        self.lbl_output_dir = ttk.Label(out_frame, text="Output: Not set", foreground="gray")
        self.lbl_output_dir.pack(side=tk.RIGHT, anchor=tk.E)

        # NEW: Side-by-side for Reference Mode and Application Mode frames
        modes_row_frame = ttk.Frame(main_frame)
        modes_row_frame.pack(fill=tk.X, pady=0)

        mode_frame2 = ttk.LabelFrame(modes_row_frame, text="Slew Rate Reference Mode", padding="4")
        mode_frame2.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))
        self.slew_mode = tk.StringVar(value="fs")
        ttk.Radiobutton(mode_frame2, text="Relative to Audio Peak (Scales with volume)", variable=self.slew_mode, value="peak").pack(anchor=tk.W)
        ttk.Radiobutton(mode_frame2, text="Relative to 0 dBFS (Absolute physical rate)", variable=self.slew_mode, value="fs").pack(anchor=tk.W)

        apply_frame = ttk.LabelFrame(modes_row_frame, text="Limiter Application Mode", padding="4")
        apply_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(5, 0))
        self.apply_mode = tk.StringVar(value="balanced")
        ttk.Radiobutton(apply_frame, text="Stereo (L to L, R to R)", variable=self.apply_mode, value="stereo").pack(anchor=tk.W)
        ttk.Radiobutton(apply_frame, text="Balanced (Apply Max to both channels)", variable=self.apply_mode, value="balanced").pack(anchor=tk.W)

        ttk.Label(main_frame, text="Oversampling Factor (Doubling OS will quadruple compute time) :").pack(anchor=tk.W, pady=(0, 0))
        self.os_exp_var = tk.DoubleVar(value=3.0)
        self.slider_over = ttk.Scale(main_frame, from_=0.0, to=12.0, variable=self.os_exp_var, orient=tk.HORIZONTAL)
        self.slider_over.pack(fill=tk.X)
        self.lbl_over_factor = ttk.Label(main_frame, text="8x")
        self.lbl_over_factor.pack(anchor=tk.E)
        self.slider_over.configure(command=self.update_slider_label)
        self.over_val = tk.IntVar(value=8)

        ttk.Label(main_frame, text="Filter Guard Band (Low-pass filter to limit aliasing during decimation. Passes frequencies flat until chosen frequency, then rolls off):").pack(anchor=tk.W, pady=(0, 0))
        self.guard_val = tk.DoubleVar(value=0.25)
        self.slider_guard = ttk.Scale(main_frame, from_=0.1, to=1.0, variable=self.guard_val, orient=tk.HORIZONTAL)
        self.slider_guard.pack(fill=tk.X)
        self.lbl_guard_val = ttk.Label(main_frame, text="0.25")
        self.lbl_guard_val.pack(anchor=tk.E)
        self.slider_guard.configure(command=self.update_guard_label)
        
        self.update_guard_label(None)

        self.btn_process = ttk.Button(main_frame, text="Process Target(s)", command=self.start_processing)
        self.btn_process.pack(fill=tk.X, pady=(0, 0))

        ttk.Label(main_frame, text="Processing Log & Progress:", font=('Helvetica', 10, 'bold')).pack(anchor=tk.W, pady=(0, 0))
        
        self.progress_overall = tk.DoubleVar()
        ttk.Progressbar(main_frame, variable=self.progress_overall, maximum=100).pack(fill=tk.X, pady=0)
        
        log_frame = ttk.Frame(main_frame)
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(2, 2))
        # Reduced height slightly to help fit everything within the window constraints
        self.status_listbox = tk.Listbox(log_frame, height=6, font=('Courier', 9))
        self.status_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))
        
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.status_listbox.yview)
        self.status_listbox.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        ttk.Button(main_frame, text="Clear Log", command=lambda: self.status_listbox.delete(0, tk.END)).pack(fill=tk.X, pady=(0, 0))

        self.lbl_status = ttk.Label(main_frame, text="Ready", font=('Helvetica', 10, 'bold'), foreground="blue")
        self.lbl_status.pack(pady=(0, 0))

    # ─── UI Callbacks ────────────────────────────────────────────────────────
    def update_slider_label(self, event):
        exp = int(round(self.os_exp_var.get()))
        exp = max(0, min(12, exp))
        os_val = 1 << exp
        self.lbl_over_factor.config(text=f"{os_val}x")
        self.over_val.set(os_val)

    def update_ram_label(self, event):
        val = self.ram_val.get()
        self.lbl_ram_val.config(text=f"{val:.1f} GB")

    def update_guard_label(self, event):
        val = self.guard_val.get()
        freq_hz = val * (GUARD_REFERENCE_FS / 2.0)
        
        if freq_hz >= 1000:
            text = f"{freq_hz/1000:.1f} kHz"
        else:
            text = f"{freq_hz:.0f} Hz"
            
        self.lbl_guard_val.config(text=text)

    def log_message(self, msg, color="black"):
        self.status_listbox.insert(tk.END, msg)
        self.status_listbox.see(tk.END)
        self.root.update_idletasks()

    # ─── File/Dir Selection ──────────────────────────────────────────────────
    def select_targets(self):
        mode = self.mode_var.get()
        if mode == "single":
            files = [filedialog.askopenfilename(filetypes=[("Audio", "*.wav *.flac *.aiff")])]
        else:
            directory = filedialog.askdirectory(title="Select Folder with Audio Files")
            if not directory: return
            extensions = ("*.wav", "*.flac", "*.aiff")
            files = []
            for ext in extensions:
                files.extend(glob.glob(os.path.join(directory, ext)))
                files.extend(glob.glob(os.path.join(directory, "**", ext), recursive=True))
            files = sorted(set(files))

        if not files or (mode == "single" and not files[0]):
            messagebox.showwarning("No Files", "No supported audio files found.")
            return

        self.target_files = files
        count = len(files)
        mode_word = "files" if count > 1 else "file"
        self.lbl_target_info.config(text=f"{count} {mode_word} selected")
        self.btn_process.config(state=tk.NORMAL)

    def select_output_dir(self):
        out = filedialog.askdirectory(title="Select Output Directory for Processed Files")
        if out:
            self.output_root = out
            self.lbl_output_dir.config(text=f"Output: {out}")
        else:
            self.output_root = None

    # ─── Preset Management ───────────────────────────────────────────────────
    def load_reference(self):
        file_path = filedialog.askopenfilename(filetypes=[("Audio Files", "*.wav *.flac *.aiff")])
        if not file_path: return
        
        self.lbl_status.config(text="Analyzing reference...", foreground="orange")
        self.root.update()
        
        slew_ratios, meta, fs = self.engine.measure_slew_rate(file_path)
        if slew_ratios is not None:
            self.current_preset = meta
            
            # Format slew_ratio (handles 1 or 2 channels)
            ratio_str = f"{meta['slew_ratio'][0]:.2f}" if meta['channels'] == 1 else f"[{meta['slew_ratio'][0]:.2f}, {meta['slew_ratio'][1]:.2f}]"
            
            # Format slew_abs (handles 1 or 2 channels)
            abs_str   = f"{meta['slew_abs'][0]:.2f}" if meta['channels'] == 1 else f"[{meta['slew_abs'][0]:.2f}, {meta['slew_abs'][1]:.2f}]"
            
            # Combine both on the same line
            val_str = f"Rel: {ratio_str}  Abs: {abs_str}"
            
            self.lbl_ref_status.config(text=f"Active: {meta['file_name']} ({val_str})", foreground="black")
            self.btn_save_preset.config(state=tk.NORMAL)
            self.lbl_status.config(text="Analysis Complete", foreground="green")
        else:
            self.lbl_status.config(text="Error: Silent file", foreground="red")

    def load_existing_preset(self):
        file_path = filedialog.askopenfilename(filetypes=[("Preset Files", "*.json")])
        if not file_path: return
        try:
            with open(file_path, 'r') as f:
                self.current_preset = json.load(f)

            ratio = self.current_preset['slew_ratio']
            abs_val = self.current_preset['slew_abs']

            val_str = f"Rel: {ratio[0]:.2f}  Abs: {abs_val[0]:.2f}" if self.current_preset['channels'] == 1 else f"Rel: [{ratio[0]:.2f}, {ratio[1]:.2f}]  Abs: [{abs_val[0]:.2f}, {abs_val[1]:.2f}]"

            self.lbl_ref_status.config(text=f"Active: {os.path.basename(file_path)} ({val_str})", foreground="blue")
            self.btn_save_preset.config(state=tk.NORMAL)
            self.lbl_status.config(text="Preset Loaded", foreground="green")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load preset: {e}")

    def save_preset(self):
        if not self.current_preset: return
        file_path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON Files", "*.json")])
        if file_path:
            with open(file_path, 'w') as f:
                json.dump(self.current_preset, f, indent=4)
            messagebox.showinfo("Success", "Preset saved!")

    # === NEW: Application Settings Persistence ===
    def _load_settings(self):
        settings_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), SETTINGS_FILENAME)
        if not os.path.exists(settings_path):
            return  # Defaults are already set via Tkinter variables in setup_ui()
        
        try:
            with open(settings_path, 'r') as f:
                saved = json.load(f)

            # RAM Limit
            ram = saved.get("ram_limit_gb", 8.0)
            self.ram_val.set(ram)
            self.update_ram_label(None)

            # Oversampling (convert value back to slider exponent index)
            os_factor = int(saved.get("oversampling_factor", 8))
            os_exp = max(0, min(12, int(np.round(np.log2(os_factor)))))
            self.os_exp_var.set(os_exp)
            self.update_slider_label(None)

            # Guard Band
            gb = saved.get("guard_band_fraction", 0.25)
            self.guard_val.set(gb)
            self.update_guard_label(None)

            # Output Directory
            self.output_root = saved.get("output_directory", "")
            if self.output_root:
                self.lbl_output_dir.config(text=f"Output: {self.output_root}")

            # Modes
            self.mode_var.set(saved.get("processing_mode", "single"))
            self.slew_mode.set(saved.get("slew_mode", "fs"))
            self.apply_mode.set(saved.get("apply_mode", "balanced"))
            self.dark_mode.set(saved.get("dark_mode", False))

        except Exception as e:
            print(f"[Settings] Failed to load from {settings_path}: {e}")

    def _save_settings(self):
        settings_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), SETTINGS_FILENAME)
        try:
            data = {
                "ram_limit_gb": self.ram_val.get(),
                "oversampling_factor": self.over_val.get(),
                "guard_band_fraction": self.guard_val.get(),
                "output_directory": self.output_root if self.output_root else "",
                "processing_mode": self.mode_var.get(),
                "slew_mode": self.slew_mode.get(),
                "apply_mode": self.apply_mode.get(),
                "dark_mode": self.dark_mode.get()
            }
            with open(settings_path, 'w') as f:
                json.dump(data, f, indent=4)
            self.lbl_status.config(text="Settings saved!", foreground="green")
            messagebox.showinfo("Settings", "Application settings saved successfully.")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save settings:\n{e}")
    # ===========================================

    # ─── Processing Router ──────────────────────────────────────────────────
    def start_processing(self):
        if self.current_preset is None:
            messagebox.showwarning("No Preset", "Please analyze a reference or load an existing preset first.")
            return
        if not self.target_files:
            messagebox.showwarning("No Targets", "Please select target file(s) first.")
            return
        if not self.output_root:
            messagebox.showwarning("No Output Dir", "Please set an output directory first.")
            return

        for w in [self.btn_process, self.btn_load_ref, self.btn_load_existing, self.btn_set_output]:
            w.config(state=tk.DISABLED)
        self.status_listbox.delete(0, tk.END)
        self.progress_overall.set(0)
        self.lbl_status.config(text="Starting...", foreground="orange")

        threading.Thread(target=self._run_processing, daemon=True).start()

    def _get_safe_worker_count(self):
        ram_limit = self.ram_val.get()
        total_ram_gb = psutil.virtual_memory().total / (1024**3)
        
        # Reserve ~1.5GB for OS + background apps as a safety margin
        available_for_app = max(4.0, total_ram_gb * 0.85)  
        required_per_worker = ram_limit
        
        if required_per_worker < 0.1: required_per_worker = 0.1
        calc_workers = int(available_for_app // required_per_worker)
        return max(1, min(calc_workers, os.cpu_count()))

    def _run_processing(self):
        try:
            mode = self.mode_var.get()
            if mode == "single":
                self._run_single_processing()
            else:
                self._run_batch_processing()
        except Exception as e:
            print(tb_module.format_exc())
            self.root.after(0, lambda: self.lbl_status.config(text="Critical Error", foreground="red"))

    def _run_single_processing(self):
        target_path = self.target_files[0]
        stem = os.path.splitext(os.path.basename(target_path))[0]
        output_path = get_unique_output_path(self.output_root, stem, ".wav")
        
        self.log_message(f"Processing single file: {os.path.basename(target_path)}")
        
        def gui_callback(step_id, percent, msg):
            log_msg = f"[{step_id}] {msg} ({percent:.1f}%)" if percent > 0 else f"[{step_id}] {msg}"
            self.root.after(0, lambda: self.status_listbox.insert(tk.END, log_msg))
            self.root.after(0, lambda p=min(percent, 100.0): self.progress_overall.set(p))
            
        try:
            self.engine.apply_slew_limit(
                target_path=target_path,
                slew_ratio_arr=self.current_preset['slew_ratio'],
                slew_abs_arr=self.current_preset['slew_abs'],
                mode=self.slew_mode.get(),
                apply_mode=self.apply_mode.get(),
                oversample_factor=self.over_val.get(),
                output_path=output_path,
                guard_band=self.guard_val.get(),
                ram_limit_gb=self.ram_val.get(),
                progress_callback=gui_callback
            )
            self.root.after(0, lambda: self._on_single_complete(True, output_path))
        except Exception as e:
            self.root.after(0, lambda: self._on_single_complete(False, str(e)))

    def _run_batch_processing(self):
        if psutil.virtual_memory().percent > 90:
            messagebox.showwarning("Low Memory", "System RAM is heavily utilized. Consider reducing the RAM slider before batch processing.")

        total_samples = 0
        workers_args = []
        for t_path in self.target_files:
            stem = os.path.splitext(os.path.basename(t_path))[0]
            o_path = get_unique_output_path(self.output_root, stem, ".wav")
            try:
                with sf.SoundFile(t_path) as f:
                    total_samples += f.frames
            except Exception:
                total_samples += 48000 * 10
            workers_args.append((t_path, o_path))

        if total_samples == 0:
            messagebox.showwarning("Empty Queue", "No audio samples found in selected files.")
            return

        completed_count = [0]
        total_workers = len(workers_args)
        global_processed = [0]
        global_lock = threading.Lock()
        
        def record_global_samples(n):
            with global_lock:
                global_processed[0] += n
            pct = min((global_processed[0] / total_samples) * 100, 100.0)
            self.root.after(0, lambda p=pct: self.progress_overall.set(p))

        self.log_message(f"Batch mode: {total_workers} files | Total samples: {total_samples:,}")
        #max_threads = min(os.cpu_count() or 2, 8)
        max_workers = self._get_safe_worker_count()
        max_workers = min(max_workers, 16)
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = []
            ram_limit_gb = self.ram_val.get()
            for t_path, o_path in workers_args:
                args = (t_path, o_path, self.current_preset, self.slew_mode.get(), 
                        self.apply_mode.get(), self.over_val.get(), self.guard_val.get(), ram_limit_gb)
                fut = executor.submit(self._process_batch_file, args, record_global_samples)
                futures.append(fut)

            for f in as_completed(futures):
                fname, status, msg = f.result()
                color = "green" if status == "SUCCESS" else "red"
                self.root.after(0, lambda m=msg, fn=fname, c=color: self.log_message(f"[{status}] {fn}: {m}", c))
                with global_lock:
                    completed_count[0] += 1

        self.root.after(0, lambda: self._on_batch_complete(completed_count[0], total_workers))

    def _process_batch_file(self, args, global_sample_adder=None):
        target_path, output_path, preset_data, mode, apply_mode, oversample_factor, guard_band, ram_limit_gb = args
        engine = SlewEngine()
        
        try:
            engine.apply_slew_limit(
                target_path=target_path,
                slew_ratio_arr=preset_data['slew_ratio'],
                slew_abs_arr=preset_data['slew_abs'],
                mode=mode,
                apply_mode=apply_mode,
                oversample_factor=oversample_factor,
                output_path=output_path,
                guard_band=guard_band,
                ram_limit_gb=ram_limit_gb,
                progress_callback=lambda sid, pct, msg: None,  # UI handled globally
                global_sample_adder=global_sample_adder        # ✅ Now correctly accepted
            )
            return os.path.basename(target_path), "SUCCESS", f"Saved to {output_path}"
        except Exception as e:
            return os.path.basename(target_path), "FAILED", tb_module.format_exc()

    def _on_single_complete(self, success, message):
        self.btn_process.config(state=tk.NORMAL)
        for w in [self.btn_load_ref, self.btn_load_existing, self.btn_set_output]:
            w.config(state=tk.NORMAL)
        
        if success:
            self.lbl_status.config(text="Finished!", foreground="green")
            messagebox.showinfo("Success", f"Single file processed and saved to:\n{message}")
        else:
            self.lbl_status.config(text="Error", foreground="red")
            messagebox.showerror("Error", message)

    def _on_batch_complete(self, done_count, total):
        self.btn_process.config(state=tk.NORMAL)
        for w in [self.btn_load_ref, self.btn_load_existing, self.btn_set_output]:
            w.config(state=tk.NORMAL)
        
        failed = total - done_count
        if failed == 0:
            self.lbl_status.config(text="Batch Complete: All successful", foreground="green")
            messagebox.showinfo("Success", f"All {total} files processed successfully!")
        else:
            self.lbl_status.config(text=f"Batch Complete: {done_count} OK, {failed} FAILED", foreground="orange")
            messagebox.showwarning("Partial Success", f"{done_count} files processed.\n{failed} failed. Check log for details.")

if __name__ == "__main__":
    root = tk.Tk()
    app = SlewGUI(root)
    root.mainloop()
