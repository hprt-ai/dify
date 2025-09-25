"""Paragraph index processor."""

import logging
import re
import threading
import uuid
from typing import Optional

import pandas as pd
from flask import Flask, current_app
from werkzeug.datastructures import FileStorage

from core.llm_generator.llm_generator import LLMGenerator
from core.rag.cleaner.clean_processor import CleanProcessor
from core.rag.datasource.retrieval_service import RetrievalService
from core.rag.datasource.vdb.vector_factory import Vector
from core.rag.extractor.entity.extract_setting import ExtractSetting
from core.rag.extractor.extract_processor import ExtractProcessor
from core.rag.index_processor.index_processor_base import BaseIndexProcessor
from core.rag.models.document import Document
from core.tools.utils.text_processing_utils import remove_leading_symbols
from libs import helper
from models.dataset import Dataset
from services.entities.knowledge_entities.knowledge_entities import Rule


class QAIndexProcessor(BaseIndexProcessor):
    def extract(self, extract_setting: ExtractSetting, **kwargs) -> list[Document]:
        text_docs = ExtractProcessor.extract(
            extract_setting=extract_setting,
            is_automatic=(
                kwargs.get("process_rule_mode") == "automatic" or kwargs.get("process_rule_mode") == "hierarchical"
            ),
        )
        return text_docs

    def transform(self, documents: list[Document], **kwargs) -> list[Document]:
        preview = kwargs.get("preview")
        process_rule = kwargs.get("process_rule")
        if not process_rule:
            raise ValueError("No process rule found.")
        if not process_rule.get("rules"):
            raise ValueError("No rules found in process rule.")
        rules = Rule(**process_rule.get("rules"))
        splitter = self._get_splitter(
            # 处理规则模式
            processing_rule_mode=process_rule.get("mode"),
            # 最大令牌数
            max_tokens=rules.segmentation.max_tokens if rules.segmentation else 0,
            # 块重叠
            chunk_overlap=rules.segmentation.chunk_overlap if rules.segmentation else 0,
            # 分隔符
            separator=rules.segmentation.separator if rules.segmentation else "",
            # 嵌入模型实例
            embedding_model_instance=kwargs.get("embedding_model_instance"),
        )

        # 将文本文档拆分成节点
        all_documents: list[Document] = []
        all_qa_documents: list[Document] = []
        
        # 检查输入文档
        if not documents:
            logging.warning("No input documents provided for QA processing")
            return []
        
        logging.info("Processing %d input documents", len(documents))
        
        for i, document in enumerate(documents):
            logging.info("Processing document %d/%d", i+1, len(documents))
            
            # 检查文档内容
            if not document.page_content or not document.page_content.strip():
                logging.warning("Document %d has empty or whitespace-only content", i+1)
                continue
            
            # 清理文档
            document_text = CleanProcessor.clean(document.page_content, kwargs.get("process_rule") or {})
            document.page_content = document_text
            
            logging.info("Document %d content length after cleaning: %d", i+1, len(document_text))
            
            # QA模式：在Transform阶段识别和格式化QA结构
            if kwargs.get("document_model") == "qa_model":
                # 规范化：全角冒号→半角，清理干扰符号
                normalized_text = (
                    document_text.replace("：", ":")
                    .replace("|", " ")
                )
                #压缩多空白
                normalized_text = re.sub(r"\s+", " ", normalized_text)
                
                # 抓取所有 QA 对（非贪婪，直到下一个 Q: 或文本结束）
                pattern = re.compile(r"Q:\s*(.*?)\s*A:\s*(.*?)(?=Q:|$)", re.DOTALL)
                matches = pattern.findall(normalized_text)
                
                qa_pairs: list[str] = []
                print(f"Unified regex found {len(matches)} matches")
                for q, a in matches:
                    question = q.strip()
                    answer = a.strip()
                    if question and answer:
                        # 输出统一为全角标签，内容不换行
                        qa_pairs.append(f"Q：{question}A：{answer}")
                
                print(f"Total QA pairs found: {len(qa_pairs)}")
                
                if qa_pairs:
                    # 用双换行符连接所有QA对，便于分段器处理
                    formatted_content = "\n\n".join(qa_pairs)
                    document.page_content = formatted_content
                    print(f"Formatted content length: {len(formatted_content)}")
                    print(f"Contains actual newlines: {chr(10) in formatted_content}")
                    print(f"Newline count: {formatted_content.count(chr(10))}")
                    print(f"First 200 chars: {formatted_content[:200]}...")
                else:
                    print("No QA pairs found, keeping original content")
                
                print("=== END QA TRANSFORM DEBUG ===")
            
            # 解析文档到节点
            document_nodes = splitter.split_documents([document])
            
            split_documents = []
            for j, document_node in enumerate(document_nodes):
                if document_node.page_content.strip():
                    doc_id = str(uuid.uuid4())
                    hash = helper.generate_text_hash(document_node.page_content)
                    if document_node.metadata is not None:
                        document_node.metadata["doc_id"] = doc_id
                        document_node.metadata["doc_hash"] = hash
                        # 标注来源于原始第几段（从1开始）
                        document_node.metadata["source_segment_index"] = j + 1
                    # 删除分隔符
                    page_content = document_node.page_content
                    document_node.page_content = remove_leading_symbols(page_content)
                    split_documents.append(document_node)
                else:
                    logging.warning("Document %d, Node %d: empty content after processing", i+1, j+1)
            all_documents.extend(split_documents)
        
        
        if preview:
            source_index = None
            if all_documents and all_documents[0].metadata is not None:
                source_index = all_documents[0].metadata.get("source_segment_index")
            self._format_qa_document(
                current_app._get_current_object(),  # type: ignore
                kwargs.get("tenant_id"),  # type: ignore
                all_documents[0],
                all_qa_documents,
                kwargs.get("doc_language", "English"),
                segment_index=source_index,
            )
        else:
            for i in range(0, len(all_documents), 10):
                threads = []
                sub_documents = all_documents[i : i + 10]
                for doc in sub_documents:
                    document_format_thread = threading.Thread(
                        target=self._format_qa_document,
                        kwargs={
                            "flask_app": current_app._get_current_object(),  # type: ignore
                            "tenant_id": kwargs.get("tenant_id"),  # type: ignore
                            "document_node": doc,
                            "all_qa_documents": all_qa_documents,
                            "document_language": kwargs.get("doc_language", "English"),
                            "segment_index": (doc.metadata or {}).get("source_segment_index"),
                        },
                    )
                    threads.append(document_format_thread)
                    document_format_thread.start()
                for thread in threads:
                    thread.join()
        # 全局最终去重：整份文件完成后再做一遍
        def _qa_key_global(doc: Document) -> tuple[str, str]:  # type: ignore[name-defined]
            q = (doc.page_content or "")
            a = ((doc.metadata or {}).get("answer") or "")
            qn = re.sub(r"\s+", "", q)
            an = re.sub(r"\s+", "", a)
            qn = re.sub(r"[。．.!！?？、,…]+$", "", qn)
            an = re.sub(r"[。．.!！?？、,…]+$", "", an)
            return qn, an

        dedup = []
        seen_keys = set()
        for d in all_qa_documents:
            k = _qa_key_global(d)
            if k in seen_keys:
                logging.info(
                    "Global dedup skipped: question='%s', answer='%s'",
                    d.page_content,
                    (d.metadata or {}).get('answer')
                )
                continue
            seen_keys.add(k)
            dedup.append(d)

        return dedup

    def format_by_template(self, file: FileStorage, **kwargs) -> list[Document]:
        # check file type
        if not file.filename or not file.filename.lower().endswith(".csv"):
            raise ValueError("Invalid file type. Only CSV files are allowed")

        try:
            # Skip the first row
            df = pd.read_csv(file)
            text_docs = []
            for index, row in df.iterrows():
                data = Document(page_content=row.iloc[0], metadata={"answer": row.iloc[1]})
                text_docs.append(data)
            if len(text_docs) == 0:
                raise ValueError("The CSV file is empty.")

        except Exception as e:
            raise ValueError(str(e))
        return text_docs

    def load(self, dataset: Dataset, documents: list[Document], with_keywords: bool = True, **kwargs):
        if dataset.indexing_technique == "high_quality":
            vector = Vector(dataset)
            vector.create(documents)

    def clean(self, dataset: Dataset, node_ids: Optional[list[str]], with_keywords: bool = True, **kwargs):
        vector = Vector(dataset)
        if node_ids:
            vector.delete_by_ids(node_ids)
        else:
            vector.delete()

    def retrieve(
        self,
        retrieval_method: str,
        query: str,
        dataset: Dataset,
        top_k: int,
        score_threshold: float,
        reranking_model: dict,
    ):
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

    def _format_qa_document(
        self, 
        flask_app: Flask, 
        tenant_id: str, 
        document_node, 
        all_qa_documents, 
        document_language, 
        segment_index: int | None = None
    ):
        format_documents = []
        if document_node.page_content is None or not document_node.page_content.strip():
            return
        with flask_app.app_context():
            try:
                # qa model document
                logging.info("document_node.page_content: %s", document_node.page_content)
                response = LLMGenerator.generate_qa_document(tenant_id, document_node.page_content, document_language)
                logging.info("LLM response: %s", response)
                document_qa_list = self._format_split_text(response)
                logging.info("document_qa_list: %s", document_qa_list)
                # 记录该段生成的 QA 数量
                try:
                    gen_count = len(document_qa_list)
                except Exception:
                    gen_count = 0
                logging.info(
                    "Segment %s generated %d QA items",
                    segment_index if segment_index is not None else '?',
                    gen_count
                )
                qa_documents = []
                for idx_within, result in enumerate(document_qa_list, start=1):
                    qa_document = Document(page_content=result["question"], metadata=document_node.metadata.copy())
                    if qa_document.metadata is not None:
                        doc_id = str(uuid.uuid4())
                        hash = helper.generate_text_hash(result["question"])
                        qa_document.metadata["answer"] = result["answer"]
                        qa_document.metadata["doc_id"] = doc_id
                        qa_document.metadata["doc_hash"] = hash
                        # 标注来源段与分段内序号
                        if segment_index is not None:
                            qa_document.metadata["source_segment_index"] = segment_index
                        qa_document.metadata["qa_index_within_segment"] = idx_within
                    qa_documents.append(qa_document)
                format_documents.extend(qa_documents)
            except Exception as e:
                logging.exception("Failed to format qa document")

            all_qa_documents.extend(format_documents)

    def _format_split_text(self, text):
        # 若存在 </think>，保留其后的正式输出内容
        if text:
            m = re.search(r"</think\s*>", text, flags=re.IGNORECASE)
            if m:
                text = text[m.end():].lstrip()

        # 按行解析，正确处理换行和空白
        qa_pairs = []
        lines = text.split('\n')
        
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            # 跳过任何遗留的 think 标签行（容错）
            if re.match(r"</?think\b", line, flags=re.IGNORECASE):
                i += 1
                continue
            
            # 查找问题行（Q1：、Q2：、Q：等，支持中英文冒号）
            if re.match(r'^Q\d*[：:]', line):
                # 提取问题内容
                question = line.split('：', 1)[-1].split(':', 1)[-1].strip()
                
                # 如果问题为空，查找下一行
                if not question:
                    i += 1
                    while i < len(lines) and not re.match(r'^[AQ]\d*[：:]', lines[i].strip()):
                        if lines[i].strip():
                            question = lines[i].strip()
                            break
                        i += 1
                
                # 查找答案行（A1：、A2：、A：等，支持中英文冒号）
                if question:
                    answer_lines = []
                    answer_started = False
                    
                    # 从下一行开始查找答案
                    j = i + 1
                    while j < len(lines):
                        current_line = lines[j].strip()
                        # 遇到 think 标签时终止该答案收集
                        if re.match(r"</?think\b", current_line, flags=re.IGNORECASE):
                            break
                        
                        # 如果遇到下一个问题，停止收集答案
                        if re.match(r'^Q\d*[：:]', current_line):
                            break
                        
                        # 如果遇到答案行，开始收集答案内容
                        if re.match(r'^A\d*[：:]', current_line):
                            answer_content = current_line.split('：', 1)[-1].split(':', 1)[-1].strip()
                            answer_started = True
                            
                            # 如果答案为空，查找下一行
                            if not answer_content:
                                j += 1
                                while j < len(lines) and not re.match(r'^[AQ]\d*[：:]', lines[j].strip()):
                                    if lines[j].strip():
                                        answer_lines.append(lines[j].strip())
                                    j += 1
                            else:
                                answer_lines.append(answer_content)
                                j += 1
                                
                                # 继续收集答案的后续行
                                while j < len(lines) and not re.match(r'^[AQ]\d*[：:]', lines[j].strip()):
                                    if lines[j].strip():
                                        answer_lines.append(lines[j].strip())
                                    j += 1
                            break
                        else:
                            j += 1
                    
                    # 组合答案内容
                    if answer_lines:
                        answer = ' '.join(answer_lines).strip()
                        # 评估用长度：去空白，去结尾标点（不影响原内容保存）
                        q_eval = re.sub(r"\s+", "", question)
                        a_eval = re.sub(r"\s+", "", answer)
                        q_eval = re.sub(r"[。．.!！?？、,…]+$", "", q_eval)
                        a_eval = re.sub(r"[。．.!！?？、,…]+$", "", a_eval)
                        if question and answer and len(q_eval) >= 2 and len(a_eval) >= 1:
                            qa_pairs.append((question, answer))
                    
                    # 更新主循环索引到答案结束位置
                    i = j
                else:
                    i += 1
            else:
                i += 1
        
        # 如果上面的方法没有解析到任何内容，尝试解析无编号格式 Q：A：
        if not qa_pairs:
            # 使用正则表达式匹配 Q：...A：... 的格式
            pattern = r'Q[：:]\s*(.*?)\s*A[：:]\s*(.*?)(?=Q[：:]|$)'
            matches = re.findall(pattern, text, re.DOTALL)
            
            for question, answer in matches:
                q_clean = question.strip()
                a_clean = answer.strip()
                
                # 清理换行符和多余空格
                q_clean = re.sub(r'\s+', ' ', q_clean)
                a_clean = re.sub(r'\s+', ' ', a_clean)
                # 评估用长度：去空白，去结尾标点（不影响原内容保存）
                q_eval = re.sub(r"\s+", "", q_clean)
                a_eval = re.sub(r"\s+", "", a_clean)
                q_eval = re.sub(r"[。．.!！?？、,…]+$", "", q_eval)
                a_eval = re.sub(r"[。．.!！?？、,…]+$", "", a_eval)
                if q_clean and a_clean and len(q_eval) >= 2 and len(a_eval) >= 1:
                    qa_pairs.append((q_clean, a_clean))

        # 局部去重与清理（同一段内可能重复）
        unique_pairs = []
        seen = set()
        for q, a in qa_pairs:
            q2 = re.sub(r"</think\s*>", "", q, flags=re.IGNORECASE).strip()
            a2 = re.sub(r"</think\s*>", "", a, flags=re.IGNORECASE).strip()
            norm_q = re.sub(r"\s+", "", re.sub(r"[。．.!！?？、,…]+$", "", q2))
            norm_a = re.sub(r"\s+", "", re.sub(r"[。．.!！?？、,…]+$", "", a2))
            key = (norm_q, norm_a)
            if key not in seen:
                seen.add(key)
                unique_pairs.append((q2, a2))
        
        logging.info("=== FORMAT SPLIT TEXT DEBUG ===")
        logging.info("Input text length: %d", len(text) if text else 0)
        logging.info("QA pairs found: %d", len(unique_pairs))
        logging.info("First few QA pairs: %s", unique_pairs[:3])
        logging.info("=== END FORMAT SPLIT TEXT DEBUG ===")
        
        return [{"question": q, "answer": re.sub(r"\n\s*", "\n", a)} for q, a in unique_pairs]
