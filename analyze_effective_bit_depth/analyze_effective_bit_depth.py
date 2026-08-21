import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import numpy as np
import soundfile as sf
import os

class BitDepthAnalyzerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Audio Effective Bit-Depth Analyzer")
        self.root.geometry("500x350")
        self.root.resizable(False, False)

        # Set a more modern look using ttk
        self.style = ttk.Style()
        self.style.configure("TButton", padding=6, font=('Segoe UI', 10))
        self.style.configure("TLabel", font=('Segoe UI', 10))

        # UI Elements
        self.main_frame = ttk.Frame(root, padding="20")
        self.main_frame.pack(expand=True, fill="both")

        self.title_label = ttk.Label(self.main_frame, text="Audio Precision Analyzer", font=('Segoe UI', 14, 'bold'))
        self.title_label.pack(pady=(0, 20))

        self.select_btn = ttk.Button(self.main_frame, text="Select WAV File", command=self.open_file)
        self.select_btn.pack(pady=10)

        # Result Area
        self.result_frame = ttk.LabelFrame(self.main_frame, text=" Analysis Results ", padding="10")
        self.result_frame.pack(expand=True, fill="both", pady=10)

        self.file_label = ttk.Label(self.result_frame, text="No file selected", wraplength=400, foreground="gray")
        self.file_label.pack(anchor="w")

        self.bits_label = ttk.Label(self.result_frame, text="Effective Bit-Depth: --", font=('Segoe UI', 11, 'bold'))
        self.bits_label.pack(anchor="w", pady=(10, 0))

        self.status_label = ttk.Label(self.result_frame, text="Status: Idle", foreground="blue")
        self.status_label.pack(anchor="w", pady=(5, 0))

    def analyze_logic(self, file_path):
        """The core mathematical analysis."""
        # Load the file
        data, samplerate = sf.read(file_path)
        
        # Analyze first channel if stereo
        if len(data.shape) > 1:
            samples = data[:, 0]
        else:
            samples = data

        # Find unique values (using a tiny tolerance for float precision)
        unique_values = np.unique(np.round(samples, decimals=12))

        if len(unique_values) < 2:
            return None, "Not enough data"

        # Calculate the quantization step
        sorted_values = np.sort(unique_values)
        diffs = np.diff(sorted_values)
        step_size = np.median(diffs)

        # --- THE FIX ---
        # Because the normalized range is [-1, 1], the total span is 2.0.
        # The formula is bits = log2(TotalSpan / step_size)
        # which is log2(2 / step_size)
        effective_bits = np.log2(2.0 / step_size)
        
        return effective_bits, step_size

    def open_file(self):
        file_path = filedialog.askopenfilename(
            title="Select an Audio File",
            filetypes=[("WAV files", "*.wav"), ("All files", "*.*")]
        )

        if not file_path:
            return 

        self.file_label.config(text=f"File: {os.path.basename(file_path)}", foreground="black")
        self.status_label.config(text="Status: Analyzing...", foreground="orange")
        self.root.update()

        try:
            bits, step = self.analyze_logic(file_path)

            if bits is None:
                messagebox.showwarning("Analysis Error", "Could not determine bit depth.")
                self.status_label.config(text="Status: Error", foreground="red")
                return

            # Display bits with 2 decimal places
            self.bits_label.config(text=f"Effective Bit-Depth: {bits:.2f} bits")
            
            # Determine conclusion
            # We use slightly wider margins to account for tiny math rounding errors
            if bits > 16.9:
                conclusion = "High Precision (24/32-bit)"
                color = "green"
            elif bits > 15.1:
                conclusion = "Effectively 16-bit"
                color = "blue"
            else:
                conclusion = "Low Precision / Heavily Quantized"
                color = "red"

            self.status_label.config(text=f"Status: {conclusion}", foreground=color)

        except Exception as e:
            messagebox.showerror("Error", f"An error occurred:\n{str(e)}")
            self.status_label.config(text="Status: Error", foreground="red")

if __name__ == "__main__":
    root = tk.Tk()
    app = BitDepthAnalyzerApp(root)
    root.mainloop()
