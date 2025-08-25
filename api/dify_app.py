from flask import Flask


class DifyApp(Flask):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user_options = {}
        self.conf = self.config
