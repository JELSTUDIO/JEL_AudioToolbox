# JEL_AudioToolbox

A collection of various audio-tools.

## Available tools
List of currently available tools (Which may contain bugs or errors I haven't discovered yet. I use AI to assist with the coding and bug-hunting):

- **analyze_effective_bit_depth**:
   - version 1.0.0
   - Open a wave-file and check the bit-depth of the actual audio-samples. Can be used to see if, for example, a 24-bit audio-file is really 24-bit audio-quality. It will report the bit-depth resolution of the actual audio-content by measuring the gaps in the sample-values.

- **slewrate_analysis_copy_limiter**:
   - version 1.0.0
   - A slew-rate limiter attempting to do so with high sound-quality. It can load audio-files and create slew-rate limit presets from them which can then be used on other files (Basically a type of copy-paste). It can batch-process so multiple files can be slew-rate limited to one slew-rate limit automatically. Files are never over-written but created with "slewed" attached to the name (If files with the same name already exist then v1, v2, v3, etc, are added to the name), in the user-chosen folder.
   - You process a finished master audio-file directly, making this slew-rate limitation the final step (Audio-levels are unchanged, so dithering should be untouched. File is exported at the same bit-depth and sample-rate as input. Since slew-rate limiting takes energy away from the audio, except at very low over-sampling factors where aliasing may contribute to the overall energy, it should never clip. At the default settings you should see a reduction of the audio-gain between 0.05 to 0.1 dB FS after slew-rate limitation)
   - Use the highest over-sampling factor you have patience for (With batch-processing you can leave it running over-night, or if you go for the max OS-factor of 4096; while you go on an extended holiday.... Batch-processing is multi-threaded, but don't expect miracles)

## Prerequisites
Before installing, ensure you have the following software installed on your Windows system:

1. **Python 3.14.7**:
   - Download from [python.org](https://www.python.org/downloads/release/python-3119/).
   - Verify (With the VENV active) with: `python --version` (should output `Python 3.14.7`).
   - The individual scripts may or may not work with other versions of Python, but the .bat file expects version 3.14 (With the VENV named 'venv314') and the scripts have only been tested with version 3.14.7.

2. **System Requirements**:
   - The scripts are only tested on Windows 11.

## Installation
Follow these steps in order to set up the project:

1. **Create a Virtual Environment**:
   ```bash
   py -3.14 -m venv venv314
   ```

2. **Activate the Virtual Environment**:
   ```bash
   venv314\Scripts\activate
   ```

3. **Installation**:
   ```bash
   pip install -r requirements.txt
   ```

## Running the Application (Without the bat-file)
1. **Activate the Virtual Environment**:
   ```bash
   cd JEL_AudioToolbox
   venv314\Scripts\activate
   ```

2. **Run the launcher**. This will open a GUI from where you click which tool to open:
   ```bash
   python launcher.py
   ```

## Running the Application (Using the bat-file on Windows)
1. **Double-click the bat-file**: 'run launcher using python314 VENV.bat'

## License
This project is licensed under the Apache License 2.0 (modified for jurisdiction) — see the LICENSE.txt file for details. A NOTICE file is included in this repo.
