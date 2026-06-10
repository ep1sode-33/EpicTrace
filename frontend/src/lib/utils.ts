import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/** 人类可读的文件大小,例如 1.2 KB / 3.4 MB。 */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "—"
  if (bytes < 1024) return `${bytes} B`
  const units = ["KB", "MB", "GB", "TB"]
  let value = bytes / 1024
  let i = 0
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024
    i++
  }
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[i]}`
}

/** ingest_method → 中文标签。 */
export function ingestMethodLabel(method: string): string {
  switch (method) {
    case "folder_scan":
      return "文件夹扫描"
    case "file_direct":
      return "直接入库"
    case "drag":
      return "拖拽"
    case "session":
      return "采集会话"
    default:
      return method
  }
}
