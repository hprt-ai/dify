from functools import wraps
from flask import request, abort
from extensions.ext_database import db
from models.model import ApiToken
from datetime import datetime


def api_key_required(f):
    """
    API Key认证装饰器
    检查请求头中的X-API-Key，验证API Key是否有效
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        api_key = request.headers.get('X-API-Key')
        if not api_key:
            abort(401, description='API Key required')
        
        # 验证API Key
        api_token = db.session.query(ApiToken).filter_by(
            token=api_key
        ).first()
        
        if not api_token:
            abort(401, description='Invalid API Key')
        
        # 更新最后使用时间
        api_token.last_used_at = datetime.utcnow()
        db.session.commit()
        
        # 将API Token信息添加到request中，供后续使用
        request.api_token = api_token
        
        return f(*args, **kwargs)
    return decorated_function


def api_key_or_login_required(f):
    """
    支持API Key或登录认证的装饰器
    优先检查API Key，如果没有则检查登录状态
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # 优先从JSON请求体获取api_key，如果没有则从Headers获取
        json_data = request.get_json() or {}
        api_key = json_data.get('api_key') or request.headers.get('X-API-Key')
        
        if api_key:
            # 验证API Key
            api_token = db.session.query(ApiToken).filter_by(
                token=api_key
            ).first()
            
            if not api_token:
                abort(401, description='Invalid API Key')
            
            # 更新最后使用时间
            api_token.last_used_at = datetime.utcnow()
            db.session.commit()
            
            # 将API Token信息添加到request中
            request.api_token = api_token
            request.auth_type = 'api_key'
            
        else:
            # 如果没有API Key，检查登录状态
            from libs.login import current_user
            if not current_user.is_authenticated:
                abort(401, description='Authentication required')
            
            request.auth_type = 'login'
        
        return f(*args, **kwargs)
    return decorated_function
