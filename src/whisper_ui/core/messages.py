"""Shared message constants used across pipeline, worker, and export layers."""

# ruff: noqa: RUF001
from __future__ import annotations

# -- Download progress messages --
DOWNLOAD_EXTRACTING_INFO = "正在取得影片資訊..."
DOWNLOAD_IN_PROGRESS = "正在下載音訊..."
DOWNLOAD_GDRIVE_IN_PROGRESS = "正在從 Google Drive 下載檔案..."
DOWNLOAD_DONE = "音訊下載完成。"
DOWNLOAD_TWITTER_RESTRICTED = "無法下載此貼文，可能需要登入、內容受限，或影片為 X 直播/Spaces（目前不支援）。"
DOWNLOAD_SOURCE_TRANSIENT = "來源暫時無法回應（可能正在限流或維護中），請稍後再試一次。"

# -- Pipeline progress messages --
PREPROCESS_CONVERTING = "正在將音訊轉換為 16kHz 單聲道 WAV..."
PREPROCESS_DONE = "音訊預處理完成。"

TRANSCRIBE_LOADING = "正在載入轉錄模型..."
TRANSCRIBE_RUNNING = "正在轉錄音訊..."
TRANSCRIBE_DONE = "轉錄完成。"

ALIGN_LOADING = "正在載入對齊模型..."
ALIGN_RUNNING = "正在對齊時間戳記..."
ALIGN_DONE = "對齊完成。"
ALIGN_SKIPPED = "對齊失敗，使用未對齊的時間戳記。"

DIARIZE_LOADING = "正在載入說話者分離模型..."
DIARIZE_RUNNING = "正在進行說話者分離..."
DIARIZE_RUNNING_HEARTBEAT = "正在進行說話者分離（已執行 {elapsed} 秒）..."
DIARIZE_DONE = "說話者分離完成。"
DIARIZE_SKIPPED = "已跳過說話者分離（未提供 HF Token）。"
DIARIZE_SKIPPED_DISABLED = "已跳過說話者分離（使用者停用）。"

ASSIGN_RUNNING = "正在分配說話者至段落..."
ASSIGN_DONE = "說話者分配完成。"
ASSIGN_SKIPPED = "已跳過說話者分配。"
ASSIGN_FAILED = "說話者分配失敗，使用未分配的段落。"

POSTPROCESS_RUNNING = "正在後處理結果..."
POSTPROCESS_DONE = "後處理完成。"
POSTPROCESS_EMPTY = "無結果可供後處理。"

LLM_CORRECTION_RUNNING = "正在進行 LLM 文字校驗（{done}/{total}）..."
LLM_CORRECTION_DONE = "LLM 文字校驗完成。"
LLM_CORRECTION_SKIPPED = "已跳過 LLM 文字校驗。"
LLM_CORRECTION_DEGRADED = "LLM 文字校驗部分失敗，已保留原文的段落："

PIPELINE_COMPLETE = "完成"
RESULT_PERSIST_FAILED = "結果儲存失敗"

# -- Export --
EXPORT_DOCX_HEADING = "轉錄結果"
