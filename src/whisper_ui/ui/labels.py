"""Centralized Traditional Chinese UI labels for Whisper UI."""
# ruff: noqa: RUF001

from __future__ import annotations

# -- Page titles --
PAGE_UPLOAD = "上傳"
PAGE_JOBS = "任務列表"
PAGE_VIEWER = "檢視器"

# -- Upload page --
UPLOAD_HEADER = "上傳音訊"
UPLOAD_DESCRIPTION = "上傳音訊或影片檔案進行語音轉錄。"
UPLOAD_SUPPORTED_FORMATS = "支援格式：{formats}"
UPLOAD_CHOOSE_FILE = "選擇檔案"
UPLOAD_LANGUAGE = "語言"
UPLOAD_MODEL = "模型"
UPLOAD_NUM_SPEAKERS = "說話者人數（0 = 自動偵測）"
UPLOAD_START = "開始轉錄"
UPLOAD_SUBMITTED = "任務已提交：**{name}**"
UPLOAD_GO_TO_JOBS = "前往**任務列表**頁面追蹤進度。"
UPLOAD_QUEUE_ERROR = "無法提交任務至佇列：{error}"
UPLOAD_NO_FILE = "請先上傳檔案。"

# -- Jobs page --
JOBS_HEADER = "任務列表"
JOBS_EMPTY = "尚無任務。前往**上傳**頁面提交檔案。"
JOBS_VIEW = "檢視"
JOBS_WAITING = "等待中..."
JOBS_ERROR = "錯誤：{error}"
JOBS_RETRY = "重新執行"
JOBS_RETRY_CONFIRM = "確定要重新執行此任務嗎？"
JOBS_RETRY_CONFIRM_BUTTON = "確認重新執行"
JOBS_RETRY_SUBMITTED = "已重新提交任務：**{name}**"
JOBS_RETRY_ERROR = "無法重新提交任務：{error}"

# -- Viewer page --
VIEWER_HEADER = "轉錄結果檢視器"
VIEWER_SELECT_JOB = "選擇已完成的任務"
VIEWER_NO_COMPLETED = "沒有已完成的任務可檢視。"
VIEWER_NOT_FOUND = "找不到該任務。"
VIEWER_NOT_COMPLETED = "任務尚未完成。狀態：{status}"
VIEWER_RESULT_NOT_FOUND = "找不到結果檔案。"
VIEWER_TRANSCRIPT_TITLE = "轉錄結果：{name}"
VIEWER_METADATA = "時長：{minutes}分{seconds}秒 | 段落：{segments} | 語言：{language}"
VIEWER_NO_SEGMENTS = "轉錄結果中無段落資料。"

# -- Status display --
STATUS_LABELS: dict[str, str] = {
    "pending": "等待中",
    "queued": "排隊中",
    "processing": "處理中",
    "completed": "已完成",
    "failed": "失敗",
}

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
