# CL34N

Removes music from video and audio files using AI. Adds a right-click option to any video or audio file on Windows.

When you right-click a file and select "Remove Music (CL34N)", it separates the audio into two files:

- The part you want to keep (vocals or music, depending on what you picked)
- The leftovers

No settings. No interface. Just right-click and it runs.

---

## Requirements

- Windows 10 or later
- NVIDIA GPU

---

## Install

Open PowerShell and run:

```
irm https://raw.githubusercontent.com/etwell/cl34n/main/install.ps1 | iex
```

Installation takes about 3-5 minutes. The first time you use it, it will download the AI model (~200 MB).

---

## Uninstall

Run the uninstaller at:

```
%LOCALAPPDATA%\CL34N\uninstall.ps1
```
