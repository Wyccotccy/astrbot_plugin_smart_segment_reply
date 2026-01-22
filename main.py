from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.core.message.message_event_result import MessageChain
from astrbot.api.message_components import Plain
from astrbot.core.conversation_mgr import Conversation  # 导入对话相关类
import aiohttp
import asyncio
import random
import re

@register(
    "astrbot_plugin_smart_segment_reply",
    "Wyccotccy",
    "通过调用硅基流动免费的大模型实现智能的分段回复，也支持自定义分段回复大模型",
    "3.0.1",  # 版本更新
    "https://github.com/Wyccotccy/astrbot_plugin_smart_segment_reply"
)
class SmartSegmentReply(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config
        self.siliconflow_key = self.config.get("siliconflow_key", "")
        self.selected_model = self.config.get("model_selection", "THUDM/GLM-4-9B-0414").strip()
        self.api_url = self.config.get("api_url", "https://api.siliconflow.cn/v1/chat/completions")
        self.session = None  # 懒加载ClientSession

    @filter.on_decorating_result()
    async def handle_segment_reply(self, event: AstrMessageEvent):
        if not self.siliconflow_key:
            logger.warning("未配置硅基流动API Key，跳过分段回复")
            return

        result = event.get_result()
        if not result or not result.chain:
            return

        # 提取原始文本（按顺序拼接，保留所有内容）
        raw_text = ""
        for comp in result.chain:
            if isinstance(comp, Plain):
                raw_text += comp.text.strip()
        raw_text = raw_text.strip()
        if not raw_text:
            return

        try:
            logger.info(f"——模型成功生成回复（原回复：{raw_text}），正在尝试分段回复中——")
            
            segments = await self.call_model_segment(raw_text)
            if not segments or len(segments) <= 1:
                logger.info(f"——分段回复成功，共分1段（无需拆分）——")
                return

            # 关键修复1：保留result.chain，填充完整分段内容（确保聊天记录保存完整回复）
            merged_segments = "\n\n".join(segments)
            result.chain = [Plain(text=merged_segments)]  # 替换为分段合并后的完整文本

            # 关键修复2：启动异步任务，延迟发送分段消息并同步到对话历史
            asyncio.create_task(self.send_segments_with_history(event, segments))
            
            logger.info(f"——分段回复任务已启动，共分{len(segments)}段——")
        except Exception as e:
            logger.error(f"分段失败，发送原消息，失败原因：{str(e)}")
            return

    async def send_segments_with_history(self, event: AstrMessageEvent, segments: list[str]):
        """延迟发送分段消息，并手动同步到对话历史"""
        umo = event.unified_msg_origin  # 获取会话唯一标识
        conv_mgr = self.context.conversation_manager  # 获取对话管理器
        curr_cid = await conv_mgr.get_curr_conversation_id(umo)  # 获取当前对话ID

        if not curr_cid:
            logger.warning("无法获取当前对话ID，分段消息仅发送不记录历史")
            for segment in segments:
                await asyncio.sleep(random.uniform(1, 3))
                await event.send(MessageChain().message(segment))
            return

        # 遍历分段消息，延迟发送并同步历史
        for segment in segments:
            await asyncio.sleep(random.uniform(1, 3))
            
            # 1. 发送分段消息
            message_chain = MessageChain().message(segment)
            await event.send(message_chain)
            
            # 2. 手动同步到对话历史
            conversation = await conv_mgr.get_conversation(umo, curr_cid)
            if not conversation:
                logger.warning(f"无法获取对话[{curr_cid}]，跳过该分段历史记录")
                continue
            
            # 构建符合格式的历史条目（与LLM消息格式一致）
            history_entry = {
                "role": "assistant",
                "content": segment,
                "timestamp": event.message_obj.timestamp  # 复用消息时间戳
            }
            
            # 更新对话历史（保留原有历史，追加新分段）
            new_history = conversation.history.copy()
            new_history.append(history_entry)
            await conv_mgr.update_conversation(
                unified_msg_origin=umo,
                conversation_id=curr_cid,
                history=new_history
            )

    async def call_model_segment(self, text: str) -> list[str]:
        # 懒加载ClientSession（避免Loop未初始化问题）
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession()

        # 安全提示词（用分隔符避免注入，保留所有原文内容）
        prompt = f"""
任务：将以下文本拆分为3-5个自然段落，严格遵循以下规则：
1. 完整保留原文所有内容（包括重复内容），不增删、不修改任何信息；
2. 按原文语义和标点断点拆分，保持上下文逻辑连贯；
3. 每段1-3句话，长度适中；
4. 仅返回分段纯文本，段落间换行分隔，禁止加序号/标记/额外内容。

### 原文开始 ###
{text}
### 原文结束 ###
        """
        payload = {
            "model": self.selected_model,
            "messages": [
                {"role": "system", "content": "仅执行文本分段任务，严格遵守规则，保留所有原文内容"},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.1,
            "max_tokens": 2048,
            "stop": ["\n\n\n"]
        }
        headers = {
            "Authorization": f"Bearer {self.siliconflow_key}",
            "Content-Type": "application/json"
        }

        async with self.session.post(self.api_url, json=payload, headers=headers) as resp:
            if resp.status != 200:
                resp_text = await resp.text()
                raise Exception(f"API请求失败（状态码{resp.status}）：{resp_text}")
            data = await resp.json()
            segment_text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            
            # 清洗分段结果（去掉序号/标记，过滤空行）
            cleaned_segments = []
            for seg in segment_text.split("\n"):
                seg = seg.strip()
                if seg:
                    # 移除开头的序号（数字、点、空格等）
                    cleaned_seg = re.sub(r'^[\d\.\s、，()【】]*', '', seg).strip()
                    if cleaned_seg:
                        cleaned_segments.append(cleaned_seg)
            return cleaned_segments

    async def terminate(self):
        # 安全关闭ClientSession
        if self.session and not self.session.closed:
            await self.session.close()
        logger.info(f"智能分段回复插件已卸载（当前模型：{self.selected_model}），资源已释放")
