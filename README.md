# JEL_AudioToolbox

A collection of various audio-tools.

## Available tools
List of currently available tools:

- **analyze_effective_bit_depth**:
   - version 1.0.0
   - Open a wave-file and check the bit-depth of the actual audio-samples. Can be used to see if, for example, a 24-bit audio-file is really 24-bit audio-quality. It will report the bit-depth resolution of the actual audio-content by measuring the gaps in the sample-values.

- **02 empty place-holder**:
   - version 1.0.0
   - Empty place-holder for future-use.

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
