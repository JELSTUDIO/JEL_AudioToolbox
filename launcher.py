import tkinter as tk
from tkinter import ttk, messagebox
import subprocess
import sys
import os

# =============================================================================
# CONFIGURATION: ADD YOUR TOOLS HERE
# To add a tool: {"name": "Display Name", "path": "folder/script.py"}
# =============================================================================
TOOLS = [
    {"name": "Audio Bit-Depth Analyzer", "path": "analyze_effective_bit_depth/analyze_effective_bit_depth.py"},
    {"name": "Slewrate Analysis Copy Limiter", "path": "slewrate_analysis_copy_limiter/slewrate_analysis_copy_limiter.py"},
    {"name": "Placeholder, do not click this one", "path": "subfolder/filename.py"},
]
# =============================================================================

class JELStudioLauncher:
    def __init__(self, root):
        self.root = root
        self.root.title("JELSTUDIO Tool Suite")
        self.root.geometry("400x500")
        self.root.resizable(False, False)

        # UI Styling
        self.style = ttk.Style()
        self.style.configure("TButton", font=('Segoe UI', 11), padding=10)
        self.style.configure("Header.TLabel", font=('Segoe UI', 16, 'bold'))

        # Main Container
        self.main_frame = ttk.Frame(root, padding="20")
        self.main_frame.pack(expand=True, fill="both")

        # Header
        self.header = ttk.Label(self.main_frame, text="JELSTUDIO Dashboard", style="Header.TLabel")
        self.header.pack(pady=(0, 20))

        # Container for buttons (to keep them organized)
        self.button_container = ttk.Frame(self.main_frame)
        self.button_container.pack(expand=True, fill="both")

        self.create_buttons()

        # Footer
        self.footer = ttk.Label(self.main_frame, text="Select a tool to launch", font=('Segoe UI', 9), foreground="gray")
        self.footer.pack(pady=(10, 0))

    def create_buttons(self):
        """Iterates through the TOOLS list and creates a button for each."""
        for tool in TOOLS:
            btn = ttk.Button(
                self.button_container, 
                text=tool["name"], 
                command=lambda t=tool: self.launch_tool(t)
            )
            btn.pack(fill="x", pady=5)

    def launch_tool(self, tool):
        """Launches the selected script using an absolute path derived from this script's location."""
        
        # 1. Get the directory where launcher.py is actually located
        # This is the "secret sauce" for GitHub portability.
        base_dir = os.path.dirname(os.path.abspath(__file__))

        # 2. Combine the base directory with the relative path from the TOOLS list
        # This turns "audio_analyzer.py" into "C:/Users/Name/Downloads/JELSTUDIO/audio_analyzer.py"
        script_path = os.path.join(base_dir, tool["path"])

        # Check if the file actually exists
        if not os.path.exists(script_path):
            messagebox.showerror("Error", f"File not found:\n{script_path}")
            return

        try:
            # Launch the tool
            subprocess.Popen([sys.executable, script_path])
            self.footer.config(text=f"Last launched: {tool['name']}", foreground="green")
        except Exception as e:
            messagebox.showerror("Launch Error", f"Failed to start {tool['name']}\n\nError: {str(e)}")

if __name__ == "__main__":
    root = tk.Tk()
    app = JELStudioLauncher(root)
    root.mainloop()
