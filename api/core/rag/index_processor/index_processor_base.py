"""Abstract interface for document loader implementations."""

from abc import ABC, abstractmethod
from typing import Optional

from configs import dify_config
from core.model_manager import ModelInstance
from core.rag.extractor.entity.extract_setting import ExtractSetting
from core.rag.models.document import Document
from core.rag.splitter.fixed_text_splitter import (
    EnhanceRecursiveCharacterTextSplitter,
    FixedRecursiveCharacterTextSplitter,
)
from core.rag.splitter.text_splitter import TextSplitter
from models.dataset import Dataset, DatasetProcessRule


class BaseIndexProcessor(ABC):
    """Interface for extract files."""

    @abstractmethod
    def extract(self, extract_setting: ExtractSetting, **kwargs) -> list[Document]:
        raise NotImplementedError

    @abstractmethod
    def transform(self, documents: list[Document], **kwargs) -> list[Document]:
        raise NotImplementedError

    @abstractmethod
    def load(self, dataset: Dataset, documents: list[Document], with_keywords: bool = True, **kwargs):
        raise NotImplementedError

    def clean(self, dataset: Dataset, node_ids: Optional[list[str]], with_keywords: bool = True, **kwargs):
        raise NotImplementedError

    @abstractmethod
    def retrieve(
        self,
        retrieval_method: str,
        query: str,
        dataset: Dataset,
        top_k: int,
        score_threshold: float,
        reranking_model: dict,
    ) -> list[Document]:
        raise NotImplementedError

    def _get_splitter(
        self,
        processing_rule_mode: str,
        max_tokens: int,
        chunk_overlap: int,
        # 分隔符
        separator: str,
        embedding_model_instance: Optional[ModelInstance],
    ) -> TextSplitter:
        """
        Get the NodeParser object according to the processing rule.
        """
        if processing_rule_mode in ["custom", "hierarchical"]:
            # 获取配置中的最大分段长度限制
            max_segmentation_tokens_length = dify_config.INDEXING_MAX_SEGMENTATION_TOKENS_LENGTH
            if max_tokens < 50 or max_tokens > max_segmentation_tokens_length:
                raise ValueError(f"Custom segment length should be between 50 and {max_segmentation_tokens_length}.")

            if separator:
                separator = separator.replace("\\n", "\n")

            character_splitter = FixedRecursiveCharacterTextSplitter.from_encoder(
                # 分段大小
                chunk_size=max_tokens,
                # 重叠大小
                chunk_overlap=chunk_overlap,
                # 分隔符
                fixed_separator=separator,
                # 分隔符列表
                separators=["\n\n", "。", ". ", " ", ""],
                # 嵌入模型实例
                embedding_model_instance=embedding_model_instance,
            )
        else:
            # 自动分段
            character_splitter = EnhanceRecursiveCharacterTextSplitter.from_encoder(
                chunk_size=DatasetProcessRule.AUTOMATIC_RULES["segmentation"]["max_tokens"],
                chunk_overlap=DatasetProcessRule.AUTOMATIC_RULES["segmentation"]["chunk_overlap"],
                separators=["\n\n", "。", ". ", " ", ""],
                embedding_model_instance=embedding_model_instance,
            )

        return character_splitter  # type: ignore
