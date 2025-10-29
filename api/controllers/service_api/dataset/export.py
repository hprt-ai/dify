import csv
import io
import json
from urllib.parse import quote

from flask import Response
from flask_restful import reqparse
from werkzeug.exceptions import BadRequest, NotFound

from controllers.service_api import api
from controllers.service_api.wraps import DatasetApiResource
from extensions.ext_database import db
from models.dataset import Dataset, Document, DocumentSegment


def safe_filename(filename):
    """安全地编码文件名，支持非ASCII字符"""
    # 使用RFC 5987标准编码文件名
    # 这将非ASCII字符编码为UTF-8，然后进行URL编码
    try:
        # 先尝试直接编码为ASCII，如果失败则使用URL编码
        filename.encode('ascii')
        # ASCII字符可以直接使用
        return f"filename=\"{filename}\""
    except UnicodeEncodeError:
        # 对于非ASCII字符，使用RFC 5987格式：filename*=UTF-8''encoded_filename
        encoded = quote(filename, safe='')
        # 同时提供ASCII回退和UTF-8编码版本
        # 先清理文件名中的特殊字符作为回退
        safe_ascii = "".join(c if ord(c) < 128 else "_" for c in filename)
        return f"filename=\"{safe_ascii}\"; filename*=UTF-8''{encoded}"


class DatasetExportApi(DatasetApiResource):
    """Resource for exporting dataset content."""

    def get(self, tenant_id, dataset_id):
        """Export dataset content in various formats."""
        return self._export(tenant_id, dataset_id)
    
    def _export(self, tenant_id, dataset_id):
        """Internal method to handle export logic."""
        # 验证数据集是否存在
        dataset_id = str(dataset_id)
        tenant_id = str(tenant_id)
        dataset = db.session.query(Dataset).where(Dataset.tenant_id == tenant_id, Dataset.id == dataset_id).first()
        if not dataset:
            raise NotFound("Dataset not found.")

        # 解析请求参数 (仅保留导出格式与文档过滤)
        parser = reqparse.RequestParser()
        parser.add_argument(
            "format",
            type=str,
            default="json",
            choices=["json", "csv", "txt", "docx", "word"],
            location="args",
        )
        parser.add_argument("document_ids", type=str, action="append", location="args")
        args = parser.parse_args()

        export_format = args["format"]
        # 默认始终导出段落与元数据
        include_segments = True
        include_metadata = True
        document_ids = args["document_ids"]

        # 获取文档列表
        query = db.session.query(Document).filter_by(dataset_id=dataset_id, tenant_id=tenant_id)
        
        # 如果指定了特定文档ID，则只导出这些文档
        if document_ids:
            query = query.filter(Document.id.in_(document_ids))
        
        documents = query.order_by(Document.created_at.desc()).all()

        if not documents:
            raise NotFound("No documents found in the dataset.")

        # 根据格式导出数据
        if export_format == "json":
            return self._export_json(dataset, documents, include_segments, include_metadata)
        elif export_format == "csv":
            return self._export_csv(dataset, documents, include_segments, include_metadata)
        elif export_format == "txt":
            return self._export_txt(dataset, documents, include_segments, include_metadata)
        elif export_format in ("docx", "word"):
            return self._export_docx(dataset, documents)
        else:
            raise BadRequest("Unsupported export format.")

    def _export_json(self, dataset, documents, include_segments, include_metadata):
        """导出为JSON格式"""
        export_data = {
            "dataset": {
                "id": dataset.id,
                "name": dataset.name,
                "description": dataset.description,
                "created_at": dataset.created_at.isoformat(),
                "updated_at": dataset.updated_at.isoformat(),
            },
            "documents": []
        }

        for document in documents:
            doc_data = {
                "id": document.id,
                "name": document.name,
                "data_source_type": document.data_source_type,
                "indexing_status": document.indexing_status,
                "created_at": document.created_at.isoformat(),
                "updated_at": document.updated_at.isoformat(),
                "enabled": document.enabled,
                "segment_count": document.segment_count,
            }

            if include_metadata:
                doc_data.update({
                    "doc_type": document.doc_type,
                    "doc_metadata": document.doc_metadata_details,
                })

            if include_segments:
                segments = db.session.query(DocumentSegment).filter_by(
                    document_id=document.id,
                    tenant_id=document.tenant_id
                ).order_by(DocumentSegment.position).all()
                
                doc_data["segments"] = []
                for segment in segments:
                    segment_data = {
                        "id": segment.id,
                        "content": segment.content,
                        "position": segment.position,
                        "word_count": segment.word_count,
                        "created_at": segment.created_at.isoformat(),
                        "updated_at": segment.updated_at.isoformat(),
                    }
                    
                    if include_metadata:
                        segment_data.update({
                            "status": segment.status,
                            "answer": segment.answer,
                            "keywords": segment.keywords,
                        })
                    
                    doc_data["segments"].append(segment_data)

            export_data["documents"].append(doc_data)

        filename = f"dataset_{dataset.name}_{dataset.id}.json"
        safe_fn = safe_filename(filename)
        response = Response(
            json.dumps(export_data, ensure_ascii=False, indent=2),
            mimetype="application/json",
            headers={
                "Content-Disposition": f"attachment; {safe_fn}",
                "Content-Type": "application/json; charset=utf-8"
            }
        )
        return response

    def _export_csv(self, dataset, documents, include_segments, include_metadata):
        """导出为CSV格式"""
        output = io.StringIO()
        writer = csv.writer(output)
        
        # 写入数据集信息
        writer.writerow(["Dataset Information"])
        writer.writerow(["ID", dataset.id])
        writer.writerow(["Name", dataset.name])
        writer.writerow(["Description", dataset.description])
        writer.writerow(["Created At", dataset.created_at.isoformat()])
        writer.writerow([])  # 空行分隔
        
        # 写入文档信息
        writer.writerow(["Documents"])
        if include_metadata:
            writer.writerow([
                "Document ID", "Name", "Data Source Type", "Indexing Status", 
                "Created At", "Updated At", "Enabled", "Segment Count", 
                "Doc Type"
            ])
        else:
            writer.writerow([
                "Document ID", "Name", "Data Source Type", "Indexing Status", 
                "Created At", "Updated At", "Enabled", "Segment Count"
            ])

        for document in documents:
            row = [
                document.id,
                document.name,
                document.data_source_type,
                document.indexing_status,
                document.created_at.isoformat(),
                document.updated_at.isoformat(),
                document.enabled,
                document.segment_count,
            ]
            
            if include_metadata:
                row.extend([
                    document.doc_type,
                ])
            
            writer.writerow(row)

        if include_segments:
            writer.writerow([])  # 空行分隔
            writer.writerow(["Segments"])
            # 段落字段顺序固定前四项：ID、position、content、answer
            if include_metadata:
                writer.writerow([
                    "Segment ID", "Position", "Content", "Answer",
                    "Document ID", "Document Name", "Word Count", "Status", "Keywords", "Created At", "Updated At"
                ])
            else:
                writer.writerow([
                    "Segment ID", "Position", "Content", "Answer",
                    "Document ID", "Document Name", "Word Count", "Created At", "Updated At"
                ])

            for document in documents:
                segments = db.session.query(DocumentSegment).filter_by(
                    document_id=document.id,
                    tenant_id=document.tenant_id
                ).order_by(DocumentSegment.position).all()
                
                for segment in segments:
                    row = [
                        segment.id,
                        segment.position,
                        segment.content,
                        (segment.answer or ""),
                    ]
                    # 后续附加其它字段
                    row.extend([
                        document.id,
                        document.name,
                        segment.word_count,
                    ])
                    if include_metadata:
                        keywords_str = json.dumps(segment.keywords, ensure_ascii=False) if segment.keywords else ""
                        row.extend([
                            segment.status,
                            keywords_str,
                        ])
                    row.extend([
                        segment.created_at.isoformat(),
                        segment.updated_at.isoformat(),
                    ])
                    writer.writerow(row)

        filename = f"dataset_{dataset.name}_{dataset.id}.csv"
        safe_fn = safe_filename(filename)
        response = Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={
                "Content-Disposition": f"attachment; {safe_fn}"
            }
        )
        return response

    def _export_txt(self, dataset, documents, include_segments, include_metadata):
        """导出为TXT格式"""
        output = io.StringIO()
        
        # 写入数据集信息
        output.write(f"Dataset: {dataset.name}\n")
        output.write(f"ID: {dataset.id}\n")
        output.write(f"Description: {dataset.description}\n")
        output.write(f"Created At: {dataset.created_at.isoformat()}\n")
        output.write(f"Updated At: {dataset.updated_at.isoformat()}\n")
        output.write("=" * 80 + "\n\n")

        for document in documents:
            # 写入文档信息
            output.write(f"Document: {document.name}\n")
            output.write(f"ID: {document.id}\n")
            output.write(f"Data Source Type: {document.data_source_type}\n")
            output.write(f"Indexing Status: {document.indexing_status}\n")
            output.write(f"Created At: {document.created_at.isoformat()}\n")
            output.write(f"Updated At: {document.updated_at.isoformat()}\n")
            output.write(f"Enabled: {document.enabled}\n")
            output.write(f"Segment Count: {document.segment_count}\n")
            
            if include_metadata:
                output.write(f"Doc Type: {document.doc_type}\n")
            
            output.write("-" * 40 + "\n")

            if include_segments:
                # 写入段落信息
                segments = db.session.query(DocumentSegment).filter_by(
                    document_id=document.id,
                    tenant_id=document.tenant_id
                ).order_by(DocumentSegment.position).all()
                
                for i, segment in enumerate(segments, 1):
                    # 固定前四项：ID、position、content、answer；再输出其余信息
                    output.write(f"Segment {i}\n")
                    output.write(f"ID: {segment.id}\n")
                    output.write(f"Position: {segment.position}\n")
                    output.write(f"Document Name: {document.name}\n")
                    output.write("Content:\n")
                    output.write(segment.content)
                    output.write("\n")
                    output.write(f"Answer: {segment.answer or ''}\n")
                    # 追加其余字段
                    output.write(f"Word Count: {segment.word_count}\n")
                    if include_metadata:
                        output.write(f"Status: {segment.status}\n")
                        if segment.keywords:
                            keywords_str = json.dumps(segment.keywords, ensure_ascii=False)
                            output.write(f"Keywords: {keywords_str}\n")
                    output.write(f"Created At: {segment.created_at.isoformat()}\n")
                    output.write(f"Updated At: {segment.updated_at.isoformat()}\n")
                    output.write("-" * 40 + "\n\n")
            
            output.write("\n" + "=" * 80 + "\n\n")

        filename = f"dataset_{dataset.name}_{dataset.id}.txt"
        safe_fn = safe_filename(filename)
        response = Response(
            output.getvalue(),
            mimetype="text/plain",
            headers={
                "Content-Disposition": f"attachment; {safe_fn}"
            }
        )
        return response

    def _export_docx(self, dataset, documents):
        try:
            from docx import Document as DocxDocument
        except Exception:
            raise BadRequest("python-docx 未安装，请先安装：pip install python-docx")

        buffer = io.BytesIO()
        doc = DocxDocument()

        doc.add_heading(f"Dataset: {dataset.name}", level=1)
        doc.add_paragraph(f"ID: {dataset.id}")
        doc.add_paragraph(f"Description: {dataset.description}")
        doc.add_paragraph(f"Created At: {dataset.created_at.isoformat()}")
        doc.add_paragraph(f"Updated At: {dataset.updated_at.isoformat()}")

        for d in documents:
            doc.add_paragraph("")
            doc.add_heading(f"Document: {d.name}", level=2)
            doc.add_paragraph(f"ID: {d.id}")
            doc.add_paragraph(f"Data Source Type: {d.data_source_type}")
            doc.add_paragraph(f"Indexing Status: {d.indexing_status}")
            doc.add_paragraph(f"Created At: {d.created_at.isoformat()}")
            doc.add_paragraph(f"Updated At: {d.updated_at.isoformat()}")
            doc.add_paragraph(f"Enabled: {d.enabled}")
            doc.add_paragraph(f"Segment Count: {d.segment_count}")
            if d.doc_type:
                doc.add_paragraph(f"Doc Type: {d.doc_type}")

            segments = (
                db.session.query(DocumentSegment)
                .filter_by(document_id=d.id, tenant_id=d.tenant_id)
                .order_by(DocumentSegment.position)
                .all()
            )

            for i, seg in enumerate(segments, 1):
                doc.add_paragraph("")
                doc.add_heading(f"Segment {i}", level=3)
                doc.add_paragraph(f"ID: {seg.id}")
                doc.add_paragraph(f"Position: {seg.position}")
                doc.add_paragraph(f"Document Name: {d.name}")
                doc.add_paragraph("Content:")
                doc.add_paragraph(seg.content or "")
                doc.add_paragraph(f"Answer: {seg.answer or ''}")
                doc.add_paragraph(f"Word Count: {seg.word_count}")
                doc.add_paragraph(f"Status: {seg.status}")
                if seg.keywords:
                    doc.add_paragraph(f"Keywords: {json.dumps(seg.keywords, ensure_ascii=False)}")
                doc.add_paragraph(f"Created At: {seg.created_at.isoformat()}")
                doc.add_paragraph(f"Updated At: {seg.updated_at.isoformat()}")

        doc.save(buffer)
        buffer.seek(0)

        filename = f"dataset_{dataset.name}_{dataset.id}.docx"
        safe_fn = safe_filename(filename)
        return Response(
            buffer.getvalue(),
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f"attachment; {safe_fn}"},
        )


# 注册API路由
api.add_resource(DatasetExportApi, "/datasets/<uuid:dataset_id>/export")
