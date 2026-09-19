import tkinter as tk
from tkinter import filedialog, messagebox
import numpy as np
import librosa
import matplotlib.pyplot as plt
from scipy.signal import correlate
import os
import json

# Configuration filename
CONFIG_FILE = "aliasing_analysis_app_settings.json"

class AliasingDetectorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Audio Aliasing Residual Detector")
        self.root.geometry("520x400")  # Slightly taller to fit new controls
        self.root.minsize(500, 400)

        self.clean_path = tk.StringVar()
        self.dirty_path = tk.StringVar()
        self.save_dir = tk.StringVar()

        # Y-Axis Scale Variables (Default: -120 dB to 0 dB)
        self.y_min_db_var = tk.StringVar(value="-120.0")
        self.y_max_db_var = tk.StringVar(value="0.0")

        # Load settings from JSON on startup
        self.load_settings()
        
        self.create_widgets()

    def load_settings(self):
        """Loads the saved directory from the JSON file."""
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r') as f:
                    data = json.load(f)  # Fixed typo from original snippet
                    if "save_dir" in data:
                        if os.path.isdir(data["save_dir"]):
                            self.save_dir.set(data["save_dir"])
            except Exception as e:
                print(f"Error loading config: {e}")

    def save_settings(self, directory):
        """Saves the current directory to the JSON file."""
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump({"save_dir": directory}, f)
        except Exception as e:
            print(f"Error saving config: {e}")

    def create_widgets(self):
        padding = {'padx': 10, 'pady': 5}

        # Clean File Selection
        tk.Label(self.root, text="Clean Sine-Sweep:").pack(anchor="w", **padding)
        clean_frame = tk.Frame(self.root)
        clean_frame.pack(fill="x", **padding)
        tk.Entry(clean_frame, textvariable=self.clean_path).pack(side="left", expand=True, fill="x")
        tk.Button(clean_frame, text="Browse", command=self.load_clean).pack(side="right")

        # Dirty File Selection
        tk.Label(self.root, text="Dirty Sine-Sweep:").pack(anchor="w", **padding)
        dirty_frame = tk.Frame(self.root)
        dirty_frame.pack(fill="x", **padding)
        tk.Entry(dirty_frame, textvariable=self.dirty_path).pack(side="left", expand=True, fill="x")
        tk.Button(dirty_frame, text="Browse", command=self.load_dirty).pack(side="right")

        # Save Directory Selection
        tk.Label(self.root, text="Save Image To:").pack(anchor="w", **padding)
        save_frame = tk.Frame(self.root)
        save_frame.pack(fill="x", **padding)
        tk.Entry(save_frame, textvariable=self.save_dir).pack(side="left", expand=True, fill="x")
        tk.Button(save_frame, text="Browse", command=self.select_save_dir).pack(side="right")

        # Y-Axis Scale Control (Min to Max)
        tk.Label(self.root, text="Y-Axis Scale (dB):").pack(anchor="w", **padding)
        y_scale_frame = tk.Frame(self.root)
        y_scale_frame.pack(fill="x", **padding)
        
        tk.Entry(y_scale_frame, textvariable=self.y_min_db_var, width=8).pack(side="left")
        tk.Label(y_scale_frame, text="  to  ").pack(side="left", padx=(2, 2))
        tk.Entry(y_scale_frame, textvariable=self.y_max_db_var, width=8).pack(side="left")
        tk.Label(y_scale_frame, text=" dB").pack(side="left", padx=(5, 0))

        # Process Button
        self.process_btn = tk.Button(self.root, text="DETECT RESIDUAL NOISE", 
                                     command=self.process_audio, 
                                     bg="#4CAF50", fg="white", font=('Helvetica', 10, 'bold'))
        self.process_btn.pack(pady=20)

    def load_clean(self):
        path = filedialog.askopenfilename(filetypes=[("Audio Files", "*.wav *.mp3 *.flac")])
        if path: self.clean_path.set(path)

    def load_dirty(self):
        path = filedialog.askopenfilename(filetypes=[("Audio Files", "*.wav *.mp3 *.flac")])
        if path: self.dirty_path.set(path)

    def select_save_dir(self):
        path = filedialog.askdirectory()
        if path: 
            self.save_dir.set(path)
            self.save_settings(path) # Save immediately when selected

    def process_audio(self):
        # Validations
        if not self.clean_path.get() or not self.dirty_path.get() or not self.save_dir.get():
            messagebox.showerror("Error", "Please select all files and a save directory.")
            return

        try:
            self.process_btn.config(text="Processing...", state="disabled")
            self.root.update()

            # 1. Load Audio
            print("Loading audio files...")
            y_clean, sr = librosa.load(self.clean_path.get(), sr=None)
            y_dirty, sr_dirty = librosa.load(self.dirty_path.get(), sr=None)

            if sr != sr_dirty:
                print(f"Resampling dirty signal from {sr_dirty} to {sr}...")
                y_dirty = librosa.resample(y_dirty, orig_sr=sr_dirty, target_sr=sr)

            # 2. Time Alignment
            print("Aligning signals...")
            corr = correlate(y_dirty, y_clean, mode='full')
            lag = np.argmax(corr) - (len(y_clean) - 1)
            
            if lag > 0:
                y_dirty_aligned = y_dirty[lag:]
            elif lag < 0:
                y_dirty_aligned = y_dirty[:lag] 
            else:
                y_dirty_aligned = y_dirty

            min_len = min(len(y_clean), len(y_dirty_aligned))
            y_clean = y_clean[:min_len]
            y_dirty_aligned = y_dirty_aligned[:min_len]

            # 3. Subtract signals
            print("Subtracting signals...")
            residual = y_dirty_aligned - y_clean

            # 4. FFT
            print("Calculating spectrum...")
            n = len(residual)
            fft_res = np.fft.rfft(residual)
            freqs = np.fft.rfftfreq(n, d=1/sr)
            magnitude_db = librosa.amplitude_to_db(np.abs(fft_res) + 1e-10, ref=np.max)

            # 5. Plotting & Scale Locking
            print("Generating image...")
            fig, ax = plt.subplots(figsize=(16, 9), dpi=100)
            ax.plot(freqs, magnitude_db, color='red', linewidth=0.5)
            ax.set_title(f"Residual Aliasing Spectrum: {os.path.basename(self.dirty_path.get())}", fontsize=16)
            ax.set_xlabel("Frequency (Hz)", fontsize=12)
            ax.set_ylabel("Magnitude (dB)", fontsize=12)

            # Parse and validate Y-axis scale
            try:
                y_min = float(self.y_min_db_var.get())
                y_max = float(self.y_max_db_var.get())
                if y_min >= y_max:
                    raise ValueError("Min must be less than Max")
            except ValueError:
                messagebox.showwarning("Scale Input", "Invalid Y-axis values entered. Defaulting to -120 dB to 0 dB.")
                y_min, y_max = -120.0, 0.0

            # Fully lock the Y-axis scale
            ax.set_ylim(y_min, y_max)

            ax.grid(True, which='both', linestyle='--', alpha=0.5)
            ax.set_xscale('log')
            ax.set_xlim(20, sr/2) 
            plt.tight_layout()

            # 6. Dynamic Filename Logic
            dirty_basename = os.path.splitext(os.path.basename(self.dirty_path.get()))[0]
            output_filename = f"{dirty_basename}_residual_spectrum.png"
            save_path = os.path.join(self.save_dir.get(), output_filename)
            
            plt.savefig(save_path, dpi=150)
            plt.close(fig)

            messagebox.showinfo("Success", 
                                f"Analysis complete!\nSaved: {output_filename}\nY-Axis Locked: {y_min} dB to {y_max} dB")

        except Exception as e:
            messagebox.showerror("Processing Error", str(e))
            print(f"Error: {e}")
        
        finally:
            self.process_btn.config(text="DETECT RESIDUAL NOISE", state="normal")

if __name__ == "__main__":
    root = tk.Tk()
    app = AliasingDetectorApp(root)
    root.mainloop()
