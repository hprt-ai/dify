import json
import logging
from flask import request, Response
from flask_login import current_user
from flask_restful import marshal, reqparse
from werkzeug.exceptions import Forbidden, InternalServerError, NotFound
from extensions.ext_database import db

import services.dataset_service
from controllers.console.app.error import (
    CompletionRequestError,
    ProviderModelCurrentlyNotSupportError,
    ProviderNotInitializeError,
    ProviderQuotaExceededError,
)
from controllers.console.datasets.error import DatasetNotInitializedError
from core.errors.error import (
    LLMBadRequestError,
    ModelCurrentlyNotSupportError,
    ProviderTokenNotInitError,
    QuotaExceededError,
)
from core.model_runtime.errors.invoke import InvokeError
from fields.hit_testing_fields import hit_testing_record_fields
from models.account import Account
from services.dataset_service import DatasetService
from services.hit_testing_service import HitTestingService

class DatasetsHitTestingBase:
    @staticmethod
    # 获取并验证数据集
    def get_and_validate_dataset(dataset_id: str):
        dataset = DatasetService.get_dataset(dataset_id)
        if dataset is None:
            raise NotFound("Dataset not found.")

        # 支持API Key和登录两种认证方式
        if hasattr(request, 'auth_type') and request.auth_type == 'api_key':
            # API Key认证，跳过权限检查（或者根据API Key的权限进行相应检查）
            pass
        else:
            # 登录认证，进行权限检查
            try:
                DatasetService.check_dataset_permission(dataset, current_user)
            except services.errors.account.NoPermissionError as e:
                raise Forbidden(str(e))

        return dataset

    @staticmethod
    # 验证召回测试的参数
    def hit_testing_args_check(args):
        HitTestingService.hit_testing_args_check(args)

    @staticmethod
    # 解析召回测试的参数
    def parse_args():
        parser = reqparse.RequestParser()

        parser.add_argument("query", type=str, location="json")
        parser.add_argument("retrieval_model", type=dict, required=False, location="json")
        parser.add_argument("external_retrieval_model", type=dict, required=False, location="json")
        return parser.parse_args()

    @staticmethod
    # 执行召回测试
    def perform_hit_testing(dataset, args):
        try:
            # 记录本次召回测试的解析参数与数据集，便于排查
            try:
                # 获取用户信息，支持API Key和登录两种方式
                user_id = None
                user_email = None
                if hasattr(request, 'auth_type') and request.auth_type == 'api_key':
                    user_id = f"API_KEY_{getattr(request.api_token, 'id', 'unknown')}"
                    user_email = "API_KEY_USER"
                else:
                    user_id = getattr(current_user, "id", None)
                    user_email = getattr(current_user, "email", None)
                
                logging.info(
                    "[hit-testing] dataset_id=%s user_id=%s user_email=%s args=%s headers=%s",
                    getattr(dataset, "id", None),
                    user_id,
                    user_email,
                    args,
                    dict(request.headers) if request else {},
                )
            except Exception as e:
                logging.warning(f"[hit-testing] Failed to log debug info: {e}")

            # 获取用户账户，支持API Key和登录两种方式
            if hasattr(request, 'auth_type') and request.auth_type == 'api_key':
                # 对于API Key认证，我们需要获取一个默认用户账户
                # 这里可以根据API Key的配置来获取相应的用户账户
                account = db.session.query(Account).first()  # 获取第一个账户作为默认值
            else:
                account = current_user

            response = HitTestingService.retrieve(
                dataset=dataset,
                query=args["query"],
                account=account,
                retrieval_model=args["retrieval_model"],
                external_retrieval_model=args["external_retrieval_model"],
                limit=10,
            )
            
            # 使用 marshal 确保中文正常显示
            result = {"query": response["query"], "records": marshal(response["records"], hit_testing_record_fields)}
            
            # 手动将 Unicode 编码转换为中文显示
            json_str = json.dumps(result, ensure_ascii=False, indent=2)
            
            # 直接返回 JSON 字符串，避免 Flask 重新序列化
            return Response(json_str, mimetype='application/json; charset=utf-8')
        except services.errors.index.IndexNotInitializedError:
            raise DatasetNotInitializedError()
        except ProviderTokenNotInitError as ex:
            raise ProviderNotInitializeError(ex.description)
        except QuotaExceededError:
            raise ProviderQuotaExceededError()
        except ModelCurrentlyNotSupportError:
            raise ProviderModelCurrentlyNotSupportError()
        except LLMBadRequestError:
            raise ProviderNotInitializeError(
                "No Embedding Model or Reranking Model available. Please configure a valid provider "
                "in the Settings -> Model Provider."
            )
        except InvokeError as e:
            raise CompletionRequestError(e.description)
        except ValueError as e:
            raise ValueError(str(e))
        except Exception as e:
            logging.exception("Hit testing failed.")
            raise InternalServerError(str(e))
