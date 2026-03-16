from __future__ import annotations

from types import SimpleNamespace

from whisper_ui.pages._upload_filter import filter_supported_files


def _fake_file(name: str) -> SimpleNamespace:
    return SimpleNamespace(name=name)


def test_filter_all_supported():
    files = [_fake_file("a.mp3"), _fake_file("b.wav"), _fake_file("c.flac")]
    supported, skipped = filter_supported_files(files)
    assert len(supported) == 3
    assert skipped == 0


def test_filter_mixed():
    files = [_fake_file("a.mp3"), _fake_file("notes.txt"), _fake_file(".DS_Store"), _fake_file("photo.jpg")]
    supported, skipped = filter_supported_files(files)
    assert len(supported) == 1
    assert supported[0].name == "a.mp3"
    assert skipped == 3


def test_filter_none_supported():
    files = [_fake_file("readme.txt"), _fake_file("image.png"), _fake_file(".DS_Store")]
    supported, skipped = filter_supported_files(files)
    assert supported == []
    assert skipped == 3


def test_filter_empty_input():
    supported, skipped = filter_supported_files([])
    assert supported == []
    assert skipped == 0


def test_filter_case_insensitive():
    files = [_fake_file("song.MP3"), _fake_file("track.Wav"), _fake_file("clip.FLAC")]
    supported, skipped = filter_supported_files(files)
    assert len(supported) == 3
    assert skipped == 0


def test_filter_nested_paths():
    files = [_fake_file("subdir/audio.mp3"), _fake_file("a/b/c/video.mp4"), _fake_file("dir/notes.txt")]
    supported, skipped = filter_supported_files(files)
    assert len(supported) == 2
    assert skipped == 1
