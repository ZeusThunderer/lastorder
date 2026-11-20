"""Record the default microphone and Minecraft-only WASAPI loopback using installed OBS.

Arguments: output.mka, seconds, window title, window class, microphone endpoint ID.
"""
import ctypes as c
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


def record(output, seconds, title, window_class, microphone):
    root = Path(os.environ["ProgramFiles"]) / "obs-studio"
    binary = root / "bin/64bit"
    dlls = os.add_dll_directory(str(binary))
    obs = c.CDLL(str(binary / "obs.dll"))
    ptr, string = c.c_void_p, c.c_char_p

    def bind(name, result, *args):
        f = getattr(obs, name)
        f.restype, f.argtypes = result, args
        return f

    class Audio(c.Structure):
        _fields_ = [("data", ptr * 8), ("frames", c.c_uint32), ("timestamp", c.c_uint64)]

    callback_type = c.CFUNCTYPE(None, ptr, ptr, c.POINTER(Audio), c.c_bool)
    if not bind("obs_startup", c.c_bool, string, string, ptr)(b"en-US", None, None):
        raise RuntimeError("libobs startup failed")
    info = (c.c_uint32 * 2)(48000, 1)
    if not bind("obs_reset_audio", c.c_bool, ptr)(c.byref(info)):
        raise RuntimeError("OBS audio initialization failed")
    module = ptr()
    path = root / "obs-plugins/64bit/win-wasapi.dll"
    result = bind("obs_open_module", c.c_int, c.POINTER(ptr), string, string)(
        c.byref(module), bytes(str(path), "utf8"), bytes(str(root / "data/obs-plugins/win-wasapi"), "utf8"))
    if result or not bind("obs_init_module", c.c_bool, ptr)(module):
        raise RuntimeError(f"WASAPI module initialization failed ({result})")
    sources = [
        ("wasapi_input_capture", {"device_id": microphone, "use_device_timing": False}),
        ("wasapi_process_output_capture", {"window": f"{title}:{window_class}:javaw.exe", "priority": 0}),
    ]
    # The audio timestamp and os_gettime_ns are in the same host clock domain.
    start = bind("os_gettime_ns", c.c_uint64)()
    capture_epoch = time.time()
    counts = [0, 0]
    handles = []
    with tempfile.TemporaryDirectory(dir=output.parent) as tmp:
        files = [open(Path(tmp) / name, "w+b") for name in ("microphone.f32", "minecraft.f32")]

        @callback_type
        def on_audio(index, source, data, muted):
            i = index or 0
            block = data.contents
            offset = round((block.timestamp - start) * 48000 / 1e9)
            if muted or not block.data[0] or not 0 <= offset < seconds * 48000:
                return
            files[i].seek(4 * offset)
            files[i].write(c.string_at(block.data[0], block.frames * 4))
            counts[i] += block.frames

        try:
            for i, (kind, settings) in enumerate(sources):
                config = bind("obs_data_create_from_json", ptr, string)(json.dumps(settings).encode())
                source = bind("obs_source_create", ptr, string, string, ptr, ptr)(
                    kind.encode(), kind.encode(), config, None)
                bind("obs_data_release", None, ptr)(config)
                if not source:
                    raise RuntimeError("Could not open " + kind)
                bind("obs_source_add_audio_capture_callback", None, ptr, callback_type, ptr)(source, on_audio, i)
                bind("obs_set_output_source", None, c.c_uint32, ptr)(i, source)
                handles.append(source)
            print("Recording started", time.time(), flush=True)
            time.sleep(seconds)
        finally:
            for i, source in enumerate(handles):
                bind("obs_source_remove_audio_capture_callback", None, ptr, callback_type, ptr)(source, on_audio, i)
                bind("obs_set_output_source", None, c.c_uint32, ptr)(i, None)
                bind("obs_source_release", None, ptr)(source)
            for f in files:
                f.close()
            bind("obs_shutdown", None)()
            dlls.close()
        if not all(counts):
            raise RuntimeError(f"Missing client track, captured frames {counts}")
        cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-n"]
        for f in files:
            cmd.extend(["-f", "f32le", "-ar", "48000", "-ac", "1", "-i", f.name])
        subprocess.run(cmd + ["-map", "0:a", "-map", "1:a", "-c:a", "flac",
                              "-metadata:s:a:0", "title=Microphone", "-metadata:s:a:1",
                              "title=Minecraft", "-metadata", f"capture_epoch={capture_epoch:.6f}",
                              str(output)], check=True)
        print("Captured frames", counts)


if __name__ == "__main__":
    record(Path(sys.argv[1]).resolve(), int(sys.argv[2]), *sys.argv[3:6])
