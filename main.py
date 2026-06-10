import asyncio
import aiohttp
import json
import os
import random
from datetime import datetime, date
from typing import Optional, Dict, List
from urllib.parse import urlencode

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter, MessageChain
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
from astrbot.api.star import Context, Star, register


@register(
    "astrbot_steps",
    "柠柚",
    "这是 AstrBot 的一个运动组手插件，可自动同步每日运动步数到主流健康运动平台",
    "1.0.0",
)
class StepsPlugin(Star):
    """
    AstrBot 步数修改插件。
    - /步数 <邮箱> <密码> <步数> 命令：修改指定账户的步数
    - /修改步数 <邮箱> <密码> <步数>：修改指定账户的步数
    """

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        # 接口与配置
        self.api_url = getattr(self.config, "api_url", "https://api.nycnm.cn/api/v2/zepplife")
        # API KEY 配置
        self.api_key = getattr(
            self.config,
            "api_key",
            "",
        )
        self.timeout = getattr(self.config, "timeout", 10)
        
        # 默认步数设置
        self.default_steps = getattr(self.config, "default_steps", 20000)
        
        # 任务存储文件路径
        self.storage_file = os.path.join(os.path.dirname(__file__), "steps_storage.json")
        # 载入已保存任务
        self.tasks: List[Dict] = self._load_storage()
        self._run_guard: Dict[str, str] = {}
        self.lock_dir = os.path.join(os.path.dirname(__file__), "locks")
        try:
            os.makedirs(self.lock_dir, exist_ok=True)
        except Exception:
            pass
        
        # 调度任务引用
        self._scheduler_task: Optional[asyncio.Task] = None
        # 启动调度器
        try:
            self._scheduler_task = asyncio.create_task(self._scheduler_loop())
        except Exception as e:
            logger.error(f"启动定时调度器失败: {e}")
        
        logger.info("步数修改插件初始化完成")

    def _is_private(self, event: AstrMessageEvent) -> bool:
        return event.is_private_chat()

    @filter.command("steps", alias={"步数", "修改步数", "运动步数"})
    async def modify_steps(self, event: AstrMessageEvent):
        """
        """
        if not self._is_private(event):
            return
        try:
            message_text = event.get_message_str().strip()
            parts = message_text.split()
            
            if len(parts) < 3:
                yield event.plain_result(
                    "❌ 用法错误！\n"
                    "正确用法：\n"
                    "• /步数 邮箱或手机号 密码 步数\n"
                    "• /步数 邮箱或手机号 密码 （使用随机 20000-30000）\n"
                )
                return
            
            account = parts[1]
            password = parts[2]
            
            # 如果没有提供步数，使用默认步数
            if len(parts) >= 4:
                try:
                    steps = int(parts[3])
                    if steps < 0 or steps > 98800:
                        yield event.plain_result("❌ 步数必须在 0-98800 之间")
                        await self._recall_command_message(event, len(parts) >= 3)
                        return
                except ValueError:
                    yield event.plain_result("❌ 步数必须是数字")
                    await self._recall_command_message(event, len(parts) >= 3)
                    return
            else:
                # 未提供步数则随机 20000-30000
                steps = random.randint(20000, 30000)
            
            # 验证邮箱或手机号
            if not self._is_valid_account(account):
                yield event.plain_result("❌ 账号格式不正确（需邮箱或手机号）")
                await self._recall_command_message(event, len(parts) >= 3)
                return
            
            yield event.plain_result(f"🏃‍♂️ 正在修改步数到 {steps}，请稍候...")
            
            # 调用API修改步数
            result = await self._modify_steps_api(account, password, steps)
            
            if result["success"]:
                yield event.plain_result(
                    f"✅ 步数修改成功！\n"
                    f"📧 账户：{account}\n"
                    f"👟 步数：{steps}\n"
                    f"💡 {result.get('message', '修改完成')}"
                )
            else:
                error_msg = result.get('message', '未知错误')
                yield event.plain_result(
                    f"❌ 步数修改失败\n"
                    f"📌 原因：{error_msg}"
                )
            await self._recall_command_message(event, True)
                
        except Exception as e:
            logger.error(f"修改步数时发生错误: {e}")
            yield event.plain_result("❌ 修改步数时发生错误，请稍后重试")
            await self._recall_command_message(event, True)

    @filter.command("steps_help", alias={"步数帮助", "运动帮助", "使用说明"})
    async def show_help(self, event: AstrMessageEvent):
        """显示步数修改插件帮助信息"""
        if not self._is_private(event):
            return
        help_text = f"""
🏃‍♂️ 步数修改插件使用说明

📝 修改步数：
• /步数 邮箱或手机号 密码 步数
  例如：/步数 example@qq.com mypassword 15000

• /步数 邮箱或手机号 密码
  未填步数时使用随机：20000-30000

🔧 支持的别名：
• /修改步数
• /运动步数

⚠️ 注意事项：
• 步数范围：0-98800（未设定则随机 20000-30000）
• 请确保邮箱和密码正确
• 支持主流健康运动平台账户
• 修改后可能需要等待几分钟同步

🔒 隐私说明：
• 插件不会保存您的账户信息
• 所有数据仅用于API调用
• 请妥善保管您的账户密码

📌 自动任务帮助：
• 查看自动任务用法与示例：/自动步数帮助
        """
        yield event.plain_result(help_text.strip())

    @filter.command("自动步数帮助", alias={"steps_auto_help", "自动任务帮助", "自动步数说明", "自动帮助"})
    async def show_auto_help(self, event: AstrMessageEvent):
        """显示自动步数任务的详细使用说明"""
        if not self._is_private(event):
            return
        help_text = f"""
🕒 自动步数任务使用说明

📥 设置自动任务：
• /设置步数任务 邮箱或手机号 密码 HH:MM [最小-最大]
  - HH:MM 为每天执行的时间，例如 08:30
  - 步数可选；不填则随机 20000-30000
  - 支持自定义范围：如 21000-26000（每日在该范围内随机）
  示例：/设置步数任务 example@qq.com mypass 08:30 21000-26000
  示例：/设置步数任务 13800138000 mypass 21:15

🗑 取消自动任务：
• /取消步数任务 邮箱或手机号
  示例：/取消步数任务 example@qq.com

👀 查看自动任务：
• /查看步数任务 [邮箱或手机号]
  无参数时显示全部任务；带参数时仅显示该账号任务
  列表中会显示：账号、执行时间、步数（范围或随机）、上次执行日期、通知状态

🔔 主动消息通知：
• 设置任务时会保存当前会话来源（unified_msg_origin）
• 定时执行后，机器人会主动向该会话发送执行结果（成功/失败、步数、时间、详情）
• 若“通知：未保存来源”，请在目标会话重新执行“/设置步数任务”以绑定来源

⚠️ 规范与限制：
• 时间格式必须为 HH:MM（24 小时制），如 08:30、21:15
• 步数范围：0-98800（未设置则随机 20000-30000）
• 账号支持邮箱或手机号（11 位、以 1 开头）
        """
        yield event.plain_result(help_text.strip())

    # =====================
    # 自动任务：存储/管理命令
    # =====================

    @filter.command("steps_set", alias={"设置步数任务", "自动步数", "设定步数"})
    async def set_auto_steps(self, event: AstrMessageEvent):
        """设置每日自动提交步数任务
        用法：/设置步数任务 邮箱或手机号 密码 HH:MM [最小-最大]
        若不填步数，则在 20000-30000 内随机
        """
        if not self._is_private(event):
            return
        try:
            parts = event.get_message_str().strip().split()
            if len(parts) < 4:
                yield event.plain_result(
                    "❌ 用法错误！\n"
                    "正确用法：\n"
                    "• /设置步数任务 邮箱或手机号 密码 HH:MM [步数]\n"
                    "• 不填步数则自动随机 20000-30000\n"
                )
                return

            email = parts[1]
            password = parts[2]
            time_str = parts[3]
            steps_min: Optional[int] = None
            steps_max: Optional[int] = None
            # 主动消息来源（统一来源标识）
            umo = event.unified_msg_origin

            # 验证邮箱或手机号
            if not self._is_valid_account(email):
                yield event.plain_result("❌ 账号格式不正确（需邮箱或手机号）")
                await self._recall_command_message(event, len(parts) >= 4)
                return

            # 验证时间格式 HH:MM
            if not self._validate_time_str(time_str):
                yield event.plain_result("❌ 时间格式不正确，请使用 HH:MM，例如 08:30")
                await self._recall_command_message(event, len(parts) >= 4)
                return

            # 校验步数范围（可选）
            if len(parts) >= 5:
                val = parts[4]
                if "-" in val:
                    try:
                        a, b = val.split("-", 1)
                        steps_min = int(a)
                        steps_max = int(b)
                        if steps_min < 0 or steps_max > 98800 or steps_min >= steps_max:
                            yield event.plain_result("❌ 步数范围需为 0-98800 且最小值小于最大值")
                            return
                    except Exception:
                        yield event.plain_result("❌ 步数范围格式错误，应为 最小-最大，如 21000-26000")
                        return
                else:
                    yield event.plain_result("❌ 不再支持固定步数，请使用范围 最小-最大")
                    return

            # 获取发送者ID
            user_id = event.get_sender_id()

            # 写入/更新任务，保存主动消息来源以便通知
            self._add_or_update_task(email=email, password=password, time_str=time_str, steps_min=steps_min, steps_max=steps_max, umo=umo, user_id=user_id)
            self._save_storage()

            if steps_min is not None and steps_max is not None:
                steps_desc = f"范围 {steps_min}-{steps_max}"
            else:
                steps_desc = "随机 20000-30000"
            yield event.plain_result(
                f"✅ 已设置每日自动提交\n📧 账户：{email}\n⏰ 时间：{time_str}\n👟 步数：{steps_desc}"
            )
            await self._recall_command_message(event, True)
        except Exception as e:
            logger.error(f"设置自动步数任务时发生错误: {e}")
            yield event.plain_result("❌ 设置任务失败，请稍后重试")
            await self._recall_command_message(event, True)

    @filter.command("steps_cancel", alias={"取消步数任务", "取消自动步数"})
    async def cancel_auto_steps(self, event: AstrMessageEvent):
        """取消自动提交任务
        用法：/取消步数任务 邮箱或手机号
        """
        if not self._is_private(event):
            return
        try:
            parts = event.get_message_str().strip().split()
            if len(parts) < 2:
                yield event.plain_result("❌ 用法错误！\n正确用法：/取消步数任务 邮箱或手机号")
                return
            email = parts[1]

            existed = self._remove_task(email)
            if existed:
                self._save_storage()
                yield event.plain_result(f"✅ 已取消自动提交任务：{email}")
            else:
                yield event.plain_result(f"ℹ️ 未找到该邮箱的自动任务：{email}")
        except Exception as e:
            logger.error(f"取消自动步数任务时发生错误: {e}")
            yield event.plain_result("❌ 取消任务失败，请稍后重试")

    @filter.command("steps_status", alias={"查看步数任务", "步数任务"})
    async def list_auto_steps(self, event: AstrMessageEvent):
        """查看当前已设置的自动任务
        用法：/查看步数任务 [邮箱或手机号]
        """
        if not self._is_private(event):
            return
        try:
            parts = event.get_message_str().strip().split()
            email_filter = parts[1] if len(parts) >= 2 else None

            tasks = self._list_tasks(email_filter)
            if not tasks:
                yield event.plain_result("ℹ️ 当前没有已设置的自动任务")
                return

            lines = ["📋 已设置的自动任务："]
            for t in tasks:
                if t.get("steps_min") is not None and t.get("steps_max") is not None:
                    steps_desc = f"范围 {t.get('steps_min')}-{t.get('steps_max')}"
                else:
                    steps_desc = "随机 20000-30000"
                last_run = t.get("last_run_date") or "从未执行"
                notify_desc = "通知：已开启" if t.get("umo") else "通知：未保存来源"
                lines.append(
                    f"• {t.get('email')} @ {t.get('time')} - 步数：{steps_desc} - 上次执行：{last_run} - {notify_desc}"
                )
            yield event.plain_result("\n".join(lines))
        except Exception as e:
            logger.error(f"查看自动步数任务时发生错误: {e}")
            yield event.plain_result("❌ 查看任务失败，请稍后重试")

    async def _modify_steps_api(self, email: str, password: str, steps: int) -> Dict:
        """
        调用步数修改API
        """
        try:
            # 构建请求参数
            params = {
                "user": email,
                "password": password,
                "steps": str(steps)
            }
            
            # 如果配置了API KEY，添加到参数中
            if self.api_key:
                params["apikey"] = self.api_key
            
            # 构建完整URL
            url = f"{self.api_url}?{urlencode(params)}"
            
            logger.info(f"请求步数修改API: {email} -> {steps} 步")
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=self.timeout)) as resp:
                    if resp.status != 200:
                        # 尝试获取响应体中的错误信息
                        error_detail = ""
                        try:
                            error_data = await resp.json()
                            if isinstance(error_data, dict):
                                error_detail = error_data.get("message", "")
                            else:
                                error_detail = str(error_data)
                        except:
                            try:
                                error_detail = await resp.text()
                            except:
                                error_detail = ""
                        
                        message = f"API请求失败，状态码：{resp.status}"
                        if error_detail:
                            message = f"API请求失败，状态码：{resp.status}，错误信息：{error_detail}"
                        
                        return {
                            "success": False,
                            "message": message
                        }
                    
                    # 尝试解析JSON响应
                    try:
                        data = await resp.json()
                    except:
                        # 如果不是JSON，尝试获取文本响应
                        text = await resp.text()
                        logger.info(f"API响应文本: {text}")
                        
                        # 根据常见的成功/失败关键词判断
                        if "成功" in text or "success" in text.lower():
                            return {
                                "success": True,
                                "message": text
                            }
                        else:
                            return {
                                "success": False,
                                "message": text
                            }
                    
                    # 处理JSON响应
                    if isinstance(data, dict):
                        # 检查是否有错误信息
                        if "error" in data:
                            error_info = data["error"]
                            if isinstance(error_info, dict):
                                return {
                                    "success": False,
                                    "message": error_info.get("message", "API返回错误")
                                }
                            else:
                                return {
                                    "success": False,
                                    "message": str(error_info)
                                }
                        
                        # 检查成功标识 - 根据实际API响应格式
                        if data.get("success") is True and data.get("code") == 200:
                            # 成功响应，提取详细信息
                            message = data.get("message", "步数修改成功")
                            data_info = data.get("data", {})
                            
                            # 构建详细的成功消息
                            if isinstance(data_info, dict):
                                steps = data_info.get("steps", "")
                                username = data_info.get("username", "")
                                if steps and username:
                                    message = f"步数修改成功！用户：{username}，步数：{steps}"
                            
                            return {
                                "success": True,
                                "message": message
                            }
                        else:
                            # 失败响应 - 优化500状态码等错误的处理
                            error_message = "步数修改失败"
                            
                            # 检查是否有具体的错误信息
                            if "message" in data:
                                error_message = data["message"]
                            elif "error" in data:
                                error_info = data["error"]
                                if isinstance(error_info, dict):
                                    error_message = error_info.get("message", str(error_info))
                                else:
                                    error_message = str(error_info)
                            
                            # 检查状态码，提供更详细的错误信息
                            code = data.get("code", 0)
                            if code == 500:
                                error_message = f"服务器内部错误：{error_message}"
                            elif code == 400:
                                error_message = f"请求参数错误：{error_message}"
                            elif code == 401:
                                error_message = f"认证失败：{error_message}"
                            elif code == 403:
                                error_message = f"权限不足：{error_message}"
                            elif code and code != 200:
                                error_message = f"错误({code})：{error_message}"
                            
                            return {
                                "success": False,
                                "message": error_message
                            }
                    else:
                        # 非字典响应，按文本处理
                        text_data = str(data)
                        if "成功" in text_data or "success" in text_data.lower():
                            return {
                                "success": True,
                                "message": text_data
                            }
                        else:
                            return {
                                "success": False,
                                "message": text_data
                            }
                            
        except asyncio.TimeoutError:
            return {
                "success": False,
                "message": "请求超时，请检查网络连接"
            }
        except Exception as e:
            logger.error(f"调用步数修改API时发生错误: {e}")
            return {
                "success": False,
                "message": f"API调用失败：{str(e)}"
            }

    async def terminate(self):
        """插件终止时的清理工作"""
        try:
            if self._scheduler_task and not self._scheduler_task.done():
                self._scheduler_task.cancel()
                # 避免未处理的取消异常
                try:
                    await self._scheduler_task
                except asyncio.CancelledError:
                    pass
        except Exception as e:
            logger.error(f"终止调度器时发生错误: {e}")
        logger.info("步数修改插件已终止")

    # =====================
    # 内部：存储/调度实现
    # =====================

    def _load_storage(self) -> List[Dict]:
        try:
            if os.path.exists(self.storage_file):
                with open(self.storage_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    tasks = data.get("tasks", [])
                    # 简单结构校验
                    return [t for t in tasks if isinstance(t, dict) and "email" in t and "password" in t and "time" in t]
        except Exception as e:
            logger.error(f"加载任务存储失败，将使用空列表: {e}")
        return []

    def _save_storage(self) -> None:
        try:
            data = {"tasks": self.tasks}
            with open(self.storage_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存任务存储失败: {e}")

    def _find_task_index(self, email: str) -> int:
        for i, t in enumerate(self.tasks):
            if t.get("email") == email:
                return i
        return -1

    def _add_or_update_task(self, email: str, password: str, time_str: str, steps_min: Optional[int], steps_max: Optional[int], umo: Optional[str], user_id: Optional[str] = None) -> None:
        idx = self._find_task_index(email)
        task = {
            "email": email,
            "password": password,
            "time": time_str,
            "steps_min": steps_min,
            "steps_max": steps_max,
            "last_run_date": None,
            "umo": umo,
            "user_id": user_id,
        }
        if idx >= 0:
            # 保留原有的 last_run_date 如果存在
            if self.tasks[idx].get("last_run_date"):
                task["last_run_date"] = self.tasks[idx]["last_run_date"]
            self.tasks[idx] = task
        else:
            self.tasks.append(task)

    def _remove_task(self, email: str) -> bool:
        idx = self._find_task_index(email)
        if idx >= 0:
            del self.tasks[idx]
            return True
        return False

    def _list_tasks(self, email: Optional[str] = None) -> List[Dict]:
        if email:
            return [t for t in self.tasks if t.get("email") == email]
        return list(self.tasks)

    def _validate_time_str(self, time_str: str) -> bool:
        try:
            datetime.strptime(time_str, "%H:%M")
            return True
        except Exception:
            return False

    async def _scheduler_loop(self):
        """简单的分钟级定时调度器：在设定时间每日执行一次"""
        logger.info("定时调度器已启动")
        # 分钟粒度轮询，避免偏差
        while True:
            try:
                now = datetime.now()
                current_time = now.strftime("%H:%M")
                today_str = date.today().isoformat()
                try:
                    for fname in os.listdir(self.lock_dir):
                        if today_str not in fname:
                            try:
                                os.remove(os.path.join(self.lock_dir, fname))
                            except Exception:
                                pass
                except Exception:
                    pass
                try:
                    self.tasks = self._load_storage()
                except Exception:
                    pass

                for task in list(self.tasks):
                    try:
                        t_time = task.get("time")
                        last_run_date = task.get("last_run_date")
                        if not t_time:
                            continue

                        if t_time == current_time and last_run_date != today_str:
                            smin = task.get("steps_min")
                            smax = task.get("steps_max")
                            if smin is not None and smax is not None:
                                steps = random.randint(int(smin), int(smax))
                            else:
                                steps = random.randint(20000, 30000)

                            email = task.get("email")
                            password = task.get("password")
                            if self._run_guard.get(email) == today_str:
                                continue
                            lock_file = os.path.join(self.lock_dir, f"{email}_{today_str}_{t_time}.lock")
                            try:
                                with open(lock_file, "x") as _:
                                    pass
                            except FileExistsError:
                                continue
                            logger.info(f"定时执行步数修改：{email} -> {steps} 步")

                            result = await self._modify_steps_api(email, password, steps)
                            # 记录日志
                            if result.get("success"):
                                logger.info(f"[自动] 步数修改成功：{email}，步数：{steps}")
                            else:
                                logger.error(f"[自动] 步数修改失败：{email}，原因：{result.get('message')}")

                            # 主动消息通知
                            try:
                                umo = task.get("umo")
                                if umo:
                                    status_text = "成功" if result.get("success") else "失败"
                                    detail = result.get("message") or ""
                                    if result.get("success"):
                                        text = (
                                            f"📣 自动步数提交通知\n"
                                            f"⏰ 执行时间：{now.strftime('%Y-%m-%d %H:%M')}\n"
                                            f"📧 账户：{email}\n"
                                            f"👟 步数：{steps}\n"
                                            f"✅ 结果：{status_text}"
                                        )
                                    else:
                                        text = (
                                            f"📣 自动步数提交通知\n"
                                            f"⏰ 执行时间：{now.strftime('%Y-%m-%d %H:%M')}\n"
                                            f"📧 账户：{email}\n"
                                            f"👟 步数：{steps}\n"
                                            f"❌ 结果：{status_text}\n"
                                            f"❗ 失败原因：{detail}"
                                        )
                                    chain = MessageChain()
                                    user_id = task.get("user_id")
                                    if user_id:
                                        chain.at(user_id).message("\n")
                                    chain.message(text)
                                    await self.context.send_message(umo, chain)
                                else:
                                    logger.info("该任务未保存主动消息来源，跳过通知发送")
                            except Exception as send_e:
                                logger.error(f"发送主动消息失败：{send_e}")

                            # 记录当日已执行
                            task["last_run_date"] = today_str
                            self._run_guard[email] = today_str
                            self._save_storage()
                    except Exception as inner_e:
                        logger.error(f"处理自动任务时发生错误: {inner_e}")

                # 30 秒轮询一次，降低资源占用
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                logger.info("定时调度器被取消")
                break
            except Exception as e:
                logger.error(f"调度器循环异常: {e}")
                await asyncio.sleep(30)

    def _is_valid_account(self, s: str) -> bool:
        """同时支持邮箱与手机号（简易校验）"""
        s = (s or "").strip()
        # 邮箱：包含 @ 和 .
        if "@" in s and "." in s:
            return True
        # 手机号：纯数字且为中国大陆常见 11 位，以 1 开头
        if s.isdigit() and len(s) == 11 and s.startswith("1"):
            return True
        return False

    async def _recall_command_message(self, event: AstrMessageEvent, contains_secret: bool) -> None:
        try:
            if not contains_secret:
                return
            if isinstance(event, AiocqhttpMessageEvent):
                msg_id = getattr(event.message_obj, "message_id", None)
                if msg_id:
                    try:
                        await event.bot.delete_msg(message_id=int(msg_id))
                    except Exception:
                        pass
        except Exception:
            pass
