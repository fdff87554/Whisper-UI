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
UPLOAD_ENABLE_DIARIZATION = "啟用說話者分離"
UPLOAD_DIARIZATION_HELP = "需要 HuggingFace Token 並接受模型使用協議"
UPLOAD_DIARIZATION_UNAVAILABLE = "說話者分離不可用（未設定 HF_TOKEN）"
UPLOAD_CONVERT_TRADITIONAL = "轉換為繁體中文"
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
JOBS_DELETE = "刪除"
JOBS_DELETE_CONFIRM = "確定要刪除此任務嗎？此操作無法復原。"
JOBS_DELETE_CONFIRM_BUTTON = "確認刪除"
JOBS_DELETE_SUCCESS = "已刪除任務：**{name}**"

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
