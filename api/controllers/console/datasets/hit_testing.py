from flask_restful import Resource
from flask import request, abort

from controllers.console import api
from controllers.console.datasets.hit_testing_base import DatasetsHitTestingBase
from controllers.console.wraps import (
    account_initialization_required,
    cloud_edition_billing_rate_limit_check,
    setup_required,
)
from libs.login import login_required
from extensions.api_key_auth import api_key_or_login_required


class HitTestingApi(Resource, DatasetsHitTestingBase):
    @api_key_or_login_required  # 支持API Key或登录认证
    @setup_required
    @account_initialization_required
    @cloud_edition_billing_rate_limit_check("knowledge")
    def post(self, dataset_id=None):
        # 支持从JSON请求体或URL路径获取数据集ID，优先从JSON请求体获取，如果没有则使用URL路径
        json_data = request.get_json() or {}
        dataset_id_from_body = json_data.get('dataset_id')
        
        if dataset_id_from_body:
            dataset_id_str = str(dataset_id_from_body)
        elif dataset_id:
            dataset_id_str = str(dataset_id)
        else:
            abort(400, description='dataset_id is required either in URL path or request body')
        
        dataset = self.get_and_validate_dataset(dataset_id_str)
        args = self.parse_args()
        self.hit_testing_args_check(args)

        return self.perform_hit_testing(dataset, args)


# 支持两种方式：URL路径或JSON请求体
api.add_resource(HitTestingApi, "/datasets/<uuid:dataset_id>/hit-testing")
api.add_resource(HitTestingApi, "/datasets/hit-testing", endpoint="hit_testing_no_path")
