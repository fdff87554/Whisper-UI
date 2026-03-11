"""Shared message constants used across pipeline, worker, and export layers."""

# ruff: noqa: RUF001
from __future__ import annotations

# -- Pipeline progress messages --
PREPROCESS_CONVERTING = "正在將音訊轉換為 16kHz 單聲道 WAV..."
PREPROCESS_DONE = "音訊預處理完成。"

TRANSCRIBE_LOADING = "正在載入轉錄模型..."
TRANSCRIBE_RUNNING = "正在轉錄音訊..."
TRANSCRIBE_DONE = "轉錄完成。"

ALIGN_LOADING = "正在載入對齊模型..."
ALIGN_RUNNING = "正在對齊時間戳記..."
ALIGN_DONE = "對齊完成。"

DIARIZE_LOADING = "正在載入說話者分離模型..."
DIARIZE_RUNNING = "正在進行說話者分離..."
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

PIPELINE_COMPLETE = "完成"

# -- Export --
EXPORT_DOCX_HEADING = "轉錄結果"
