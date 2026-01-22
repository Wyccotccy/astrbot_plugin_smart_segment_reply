from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.core.message.message_event_result import MessageChain
from astrbot.api.message_components import Plain
from astrbot.core.conversation_mgr import Conversation
import aiohttp
import asyncio
import random
import re

@register(
    "astrbot_plugin_smart_segment_reply",
    "Wyccotccy",
    "通过调用硅基流动免费的大模型实现智能的分段回复，也支持自定义分段回复大模型",
    "3.0.3",  # 版本更新
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
            
            # 先尝试模型分段，失败则手动兜底
            segments = await self.call_model_segment(raw_text)
            # 兜底逻辑：模型未拆分时，手动按标点拆分（针对短文本/古诗）
            if not segments or len(segments) <= 1:
                logger.info("——模型未拆分，启用手动兜底分段——")
                segments = self.manual_segment(raw_text)
                if len(segments) <= 1:
                    logger.info("——文本过短，无需分段——")
                    return

            # 禁用默认发送，仅靠异步任务发送分段
            result.chain.clear()

            # 启动分段发送+历史同步
            asyncio.create_task(self.send_segments_with_history(event, segments))
            
            logger.info(f"——分段回复任务已启动，共分{len(segments)}段——")
        except Exception as e:
            logger.error(f"分段失败，发送原消息，失败原因：{str(e)}")
            result.chain = [Plain(text=raw_text)]
            return

    def manual_segment(self, text: str) -> list[str]:
        """手动兜底分段（针对模型不分段的短文本/古诗）"""
        # 按中文标点拆分（逗号、句号、分号），保留语义连贯
        split_pattern = r'([，。；；！？])'
        parts = re.split(split_pattern, text)
        # 重组分段（标点+文本合并）
        segments = []
        current = ""
        for part in parts:
            if part in ['，', '。', '；', '！', '？']:
                if current:
                    current += part
                    segments.append(current.strip())
                    current = ""
            else:
                current += part
        # 补充最后一段（无标点结尾）
        if current.strip():
            segments.append(current.strip())
        # 过滤空分段，确保至少2段才生效
        return [seg for seg in segments if seg] if len(segments) >=2 else [text]

    async def send_segments_with_history(self, event: AstrMessageEvent, segments: list[str]):
        """延迟发送分段消息，并同步历史"""
        umo = event.unified_msg_origin
        conv_mgr = self.context.conversation_manager
        curr_cid = await conv_mgr.get_curr_conversation_id(umo)

        for idx, segment in enumerate(segments):
            # 优化发送间隔：首段稍长，后续缩短
            delay = random.uniform(1.5, 2.5) if idx == 0 else random.uniform(0.8, 1.5)
            await asyncio.sleep(delay)
            
            # 发送分段（去除多余标点）
            clean_segment = re.sub(r'[，,。；;、]+$', '', segment).strip()
            await event.send(MessageChain().message(clean_segment))
            
            # 同步历史
            if curr_cid:
                conversation = await conv_mgr.get_conversation(umo, curr_cid)
                if conversation:
                    new_history = conversation.history.copy()
                    new_history.append({
                        "role": "assistant",
                        "content": clean_segment,
                        "timestamp": event.message_obj.timestamp + idx * 1000  # 避免时间戳重复
                    })
                    await conv_mgr.update_conversation(umo, curr_cid, history=new_history)

    async def call_model_segment(self, text: str) -> list[str]:
        """优化模型分段逻辑，确保拆分生效"""
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession()

        # 关键优化：提示词添加拆分示例，提高temperature，明确短文本拆分规则
        prompt = f"""
任务：将以下文本拆分为2-4个自然段落（短文本优先拆分为2段），严格遵循规则：
1. 完整保留原文所有内容，不增删、不修改；
2. 按原文标点（逗号、句号）断点拆分，每段1句（短文本）或1-2句（长文本）；
3. 仅返回分段纯文本，段落间换行分隔，禁止任何序号、标记、额外说明；
4. 示例：原文“日照香炉生紫烟，遥看瀑布挂前川。飞流直下三千尺，疑是银河落九天。”
   拆分结果：
   日照香炉生紫烟，遥看瀑布挂前川。
   飞流直下三千尺，疑是银河落九天。

### 原文开始 ###
{text}
### 原文结束 ###
        """
        payload = {
            "model": self.selected_model,
            "messages": [
                {"role": "system", "content": "仅执行文本分段任务，短文本必须拆分，严格按示例格式输出"},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.2,  # 提高随机性，确保拆分
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
                logger.error(f"API请求失败：{resp.status} - {resp_text}")
                return []
            data = await resp.json()
            segment_text = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            
            # 简化清洗逻辑：只去序号和空行，不轻易过滤分段
            cleaned_segments = []
            for seg in segment_text.split("\n"):
                seg = seg.strip()
                if not seg:
                    continue
                # 仅移除开头的序号（如“1. ”“一、”）
                cleaned_seg = re.sub(r'^[\d一二三四五六七八九十\.\s、，()【】]*', '', seg).strip()
                if cleaned_seg:
                    cleaned_segments.append(cleaned_seg)
            
            logger.info(f"模型分段结果：{cleaned_segments}")
            return cleaned_segments

    async def terminate(self):
        if self.session and not self.session.closed:
            await self.session.close()
        logger.info(f"智能分段回复插件已卸载（当前模型：{self.selected_model}），资源已释放")
