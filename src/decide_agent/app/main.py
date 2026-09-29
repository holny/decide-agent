"""App main：真入口（组装根）——把 build_app 注入通道马甲，依赖方向 app → channel。"""
from decide_agent.app.entry import build_app
from decide_agent.channel.cli.main import create_cli_app

app = create_cli_app(build_app)

if __name__ == "__main__":
    app()
