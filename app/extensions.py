from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager
from flask_mail import Mail

db = SQLAlchemy(engine_options={
    "pool_pre_ping": True,
    "pool_size": 5,
    "max_overflow": 10,
})
migrate = Migrate()
login_manager = LoginManager()
mail = Mail()
