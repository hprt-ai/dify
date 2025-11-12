"""Paragraph index processor."""

import uuid
import re
from typing import Optional

from core.rag.cleaner.clean_processor import CleanProcessor
from core.rag.datasource.keyword.keyword_factory import Keyword
from core.rag.datasource.retrieval_service import RetrievalService
from core.rag.datasource.vdb.vector_factory import Vector
from core.rag.extractor.entity.extract_setting import ExtractSetting
from core.rag.extractor.extract_processor import ExtractProcessor
from core.rag.index_processor.index_processor_base import BaseIndexProcessor
from core.rag.models.document import Document
from core.tools.utils.text_processing_utils import remove_leading_symbols
from libs import helper
from models.dataset import Dataset, DatasetProcessRule
from services.entities.knowledge_entities.knowledge_entities import Rule


class ParagraphIndexProcessor(BaseIndexProcessor):
    def extract(self, extract_setting: ExtractSetting, **kwargs) -> list[Document]:
        text_docs = ExtractProcessor.extract(
            extract_setting=extract_setting,
            is_automatic=(
                kwargs.get("process_rule_mode") == "automatic" or kwargs.get("process_rule_mode") == "hierarchical"
            ),
        )

        return text_docs

    def transform(self, documents: list[Document], **kwargs) -> list[Document]:
        process_rule = kwargs.get("process_rule")
        if not process_rule:
            raise ValueError("No process rule found.")
        if process_rule.get("mode") == "automatic":
            automatic_rule = DatasetProcessRule.AUTOMATIC_RULES
            rules = Rule(**automatic_rule)
        else:
            # 使用自定义分段规则
            if not process_rule.get("rules"):
                raise ValueError("No rules found in process rule.")
            rules = Rule(**process_rule.get("rules"))

        # 获取 parsing_mode
        parsing_mode = rules.parsing_mode if rules.parsing_mode else "default"

        # Split the text documents into nodes.
        if not rules.segmentation:
            raise ValueError("No segmentation found in rules.")
        splitter = self._get_splitter(
            processing_rule_mode=process_rule.get("mode"),
            max_tokens=rules.segmentation.max_tokens,
            # 重叠大小
            chunk_overlap=rules.segmentation.chunk_overlap,
            # 分隔符
            separator=rules.segmentation.separator,
            embedding_model_instance=kwargs.get("embedding_model_instance"),
        )
        all_documents = []
        for document in documents:
            # 获取文档来源
            source = document.metadata.get("source", "")
            is_docx = source.lower().endswith(".docx")

            # 确定当前文档的实际处理模式
            # 对于 qa_docx 和 full_docx 模式，只对 .docx 文件生效，其他文件使用 default 模式
            effective_mode = parsing_mode
            if parsing_mode in ["qa_docx", "full_docx"] and not is_docx:
                effective_mode = "default"

            if effective_mode == "qa_docx":
                # QA(docx) 模式的特殊处理逻辑：按"问题X"进行分段
                # 清理文档内容
                document_text = CleanProcessor.clean(document.page_content, kwargs.get("process_rule", {}))

                # 匹配"问题"后跟数字（可能有前导0）和冒号的模式
                # 支持: 问题1: 问题01: 问题1： 问题01：
                pattern = r'(问题\d+[：:])'
                # 分割文本，保留分隔符
                parts = re.split(pattern, document_text)

                # 重组分段：将"问题X："与其后的内容合并
                segments = []
                i = 0
                while i < len(parts):
                    if i == 0 and parts[i].strip() and not re.match(pattern, parts[i]):
                        # 第一部分如果不是"问题X："开头，跳过（通常是标题）
                        i += 1
                    elif i < len(parts) - 1 and re.match(pattern, parts[i]):
                        # 将"问题X："与其后的内容合并
                        segment = parts[i] + (parts[i + 1] if i + 1 < len(parts) else "")
                        segments.append(segment.strip())
                        i += 2
                    else:
                        # 跳过其他部分
                        i += 1

                # 为每个分段创建 Document
                split_documents = []
                for segment in segments:
                    if segment.strip():
                        # 生成文档ID
                        doc_id = str(uuid.uuid4())
                        # 生成文档哈希值
                        hash = helper.generate_text_hash(segment)

                        # 创建新的 Document
                        segment_doc = Document(
                            page_content=segment,
                            metadata={
                                **document.metadata,
                                "doc_id": doc_id,
                                "doc_hash": hash
                            }
                        )
                        split_documents.append(segment_doc)

                all_documents.extend(split_documents)
            elif effective_mode == "full_docx":
                # full_docx 模式：将整篇文档作为一个分段返回
                # 清理文档内容
                document_text = CleanProcessor.clean(document.page_content, kwargs.get("process_rule", {}))

                # 生成文档ID
                doc_id = str(uuid.uuid4())
                # 生成文档哈希值
                hash = helper.generate_text_hash(document_text)

                # 创建单个 Document（整篇文档）
                full_doc = Document(
                    page_content=document_text,
                    metadata={
                        **document.metadata,
                        "doc_id": doc_id,
                        "doc_hash": hash
                    }
                )
                all_documents.append(full_doc)
            else:
                # 默认模式：使用原来的逻辑
                # 清理文档内容
                document_text = CleanProcessor.clean(document.page_content, kwargs.get("process_rule", {}))
                document.page_content = document_text
                # 切割文档
                document_nodes = splitter.split_documents([document])
                split_documents = []
                for document_node in document_nodes:
                    if document_node.page_content.strip():
                        # 生成文档ID
                        doc_id = str(uuid.uuid4())
                        # 生成文档哈希值
                        hash = helper.generate_text_hash(document_node.page_content)
                        # 添加文档ID和哈希值到元数据
                        if document_node.metadata is not None:
                            document_node.metadata["doc_id"] = doc_id
                            document_node.metadata["doc_hash"] = hash
                        # 删除分隔符
                        page_content = remove_leading_symbols(document_node.page_content).strip()
                        if len(page_content) > 0:
                            document_node.page_content = page_content
                            split_documents.append(document_node)
                all_documents.extend(split_documents)
        return all_documents

    def load(self, dataset: Dataset, documents: list[Document], with_keywords: bool = True, **kwargs):
        if dataset.indexing_technique == "high_quality":
            vector = Vector(dataset)
            vector.create(documents)
            with_keywords = False
        if with_keywords:
            keywords_list = kwargs.get("keywords_list")
            keyword = Keyword(dataset)
            if keywords_list and len(keywords_list) > 0:
                keyword.add_texts(documents, keywords_list=keywords_list)
            else:
                keyword.add_texts(documents)

    def clean(self, dataset: Dataset, node_ids: Optional[list[str]], with_keywords: bool = True, **kwargs):
        if dataset.indexing_technique == "high_quality":
            vector = Vector(dataset)
            if node_ids:
                vector.delete_by_ids(node_ids)
            else:
                vector.delete()
            with_keywords = False
        if with_keywords:
            keyword = Keyword(dataset)
            if node_ids:
                keyword.delete_by_ids(node_ids)
            else:
                keyword.delete()

    def retrieve(
        self,
        retrieval_method: str,
        query: str,
        dataset: Dataset,
        top_k: int,
        score_threshold: float,
        reranking_model: dict,
    ) -> list[Document]:
        # Set search parameters.
        results = RetrievalService.retrieve(
            retrieval_method=retrieval_method,
            dataset_id=dataset.id,
            query=query,
            top_k=top_k,
            score_threshold=score_threshold,
            reranking_model=reranking_model,
        )
        # Organize results.
        docs = []
        for result in results:
            metadata = result.metadata
            metadata["score"] = result.score
            if result.score > score_threshold:
                doc = Document(page_content=result.page_content, metadata=metadata)
                docs.append(doc)
        return docs
