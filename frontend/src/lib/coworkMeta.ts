import { type CoworkSession } from "@/lib/api";

/** 会话状态 → 徽标文案。 */
export const STATUS_LABELS: Record<string, string> = {
  idle: "空闲",
  thinking: "思考中",
  executing: "执行中",
  waiting_approval: "待确认",
  done: "完成",
  error: "错误",
};

/** 会话状态 → 徽标变体:运行态琥珀、完成绿、错误红(用 className 补 destructive 配色)、其余次要。 */
export function statusBadgeProps(status: string): {
  variant: "secondary" | "pending" | "success";
  className?: string;
} {
  switch (status) {
    case "thinking":
    case "executing":
    case "waiting_approval":
      return { variant: "pending" };
    case "done":
      return { variant: "success" };
    case "error":
      return {
        variant: "secondary",
        className: "border-destructive/25 bg-destructive/10 text-destructive",
      };
    default:
      return { variant: "secondary" };
  }
}

/** 会话显示名:无名(未首轮自动标题)回退「会话 #id」。 */
export function sessionTitle(s: CoworkSession): string {
  return s.name?.trim() || `会话 #${s.id}`;
}
