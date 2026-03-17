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
UPLOAD_CONVERT_TRADITIONAL_HELP = "將簡體中文轉錄結果轉換為繁體中文（僅對中文生效）"
UPLOAD_START = "開始轉錄"
UPLOAD_SUBMITTED = "任務已提交：**{name}**"
UPLOAD_GO_TO_JOBS = "前往**任務列表**頁面追蹤進度。"
UPLOAD_QUEUE_ERROR = "無法提交任務至佇列：{error}"
UPLOAD_NO_FILE = "請先上傳檔案。"
UPLOAD_BATCH_SUBMITTED = "已提交 {count} 個任務"
UPLOAD_BATCH_EXCEEDS_LIMIT = "最多一次上傳 {limit} 個檔案，目前已選 {count} 個。"
UPLOAD_TAB_FILES = "選擇檔案"
UPLOAD_TAB_FOLDER = "選擇資料夾"
UPLOAD_CHOOSE_FOLDER = "選擇資料夾"
UPLOAD_FOLDER_DESCRIPTION = "選擇包含音訊或影片檔案的資料夾，將自動篩選支援的格式並批次轉錄。"
UPLOAD_FOLDER_FILTERED = "已自動篩選：略過 {skipped} 個不支援的檔案，保留 {remaining} 個支援的檔案。"
UPLOAD_NO_SUPPORTED_FILES = "所選檔案中沒有支援的格式。"
UPLOAD_DRAG_DROP = "將檔案拖曳至此處，或點擊下方選擇檔案"
UPLOAD_UPLOADING = "上傳中..."
UPLOAD_FILE_TOO_LARGE = "檔案「{name}」超過大小限制（{limit}）。"
UPLOAD_INVALID_LANGUAGE = "不支援的語言：{value}"
UPLOAD_INVALID_MODEL = "不支援的模型：{value}"

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
JOBS_BATCH_LABEL = "批次上傳（{count} 個檔案）"
JOBS_BATCH_PROGRESS = "{completed}/{total} 已完成"
JOBS_BATCH_RETRY_ALL = "全部重試"
JOBS_BATCH_RETRY_ALL_CONFIRM = "確定要重新執行此批次中所有失敗的任務嗎？"
JOBS_BATCH_RETRY_ALL_CONFIRM_BUTTON = "確認全部重試"
JOBS_BATCH_DELETE_ALL = "全部刪除"
JOBS_BATCH_DELETE_ALL_CONFIRM = "確定要刪除此批次中所有任務嗎？此操作無法復原。"
JOBS_BATCH_DELETE_ALL_CONFIRM_BUTTON = "確認全部刪除"
JOBS_BATCH_RETRY_ALL_SUBMITTED = "已重新提交 {count} 個失敗任務"
JOBS_BATCH_DELETE_ALL_SUCCESS = "已刪除整個批次（{count} 個任務）"
JOBS_BATCH_DOWNLOAD = "批次下載"
JOBS_BATCH_DOWNLOAD_FORMAT = "選擇匯出格式"
JOBS_BATCH_DOWNLOAD_BUTTON = "下載 ZIP"
JOBS_FILTER_ALL = "全部"
JOBS_FILTER_LABEL = "篩選狀態"
JOBS_EMPTY_FILTERED = "沒有符合篩選條件的任務。"
JOBS_PAGE_INFO = "第 {current} / {total} 頁（共 {count} 個任務）"
JOBS_PAGE_PREV = "上一頁"
JOBS_PAGE_NEXT = "下一頁"
JOBS_STALE_ERROR = "任務逾時或 Worker 異常終止"

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
VIEWER_SEARCH_PLACEHOLDER = "輸入關鍵字篩選..."
VIEWER_SEARCH_NO_RESULTS = "找不到符合的段落。"
VIEWER_EMPTY_STATE = "請從上方選擇一個已完成的任務來檢視轉錄結果。"
VIEWER_COPY = "複製全文"
VIEWER_COPIED = "已複製"
VIEWER_EXPORT_TOOLTIPS: dict[str, str] = {
    "srt": "SubRip 字幕格式",
    "vtt": "WebVTT 字幕格式",
    "txt": "純文字格式",
    "json": "JSON 結構化資料",
    "docx": "Word 文件格式",
}

# -- Theme --
THEME_TOGGLE_LIGHT = "淺色模式"
THEME_TOGGLE_DARK = "深色模式"

# -- Dialog --
DIALOG_CANCEL = "取消"
DIALOG_CONFIRM = "確認"

# -- Status display --
STATUS_LABELS: dict[str, str] = {
    "pending": "等待中",
    "queued": "排隊中",
    "processing": "處理中",
    "completed": "已完成",
    "failed": "失敗",
}
