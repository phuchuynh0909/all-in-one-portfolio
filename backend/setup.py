from setuptools import setup, find_packages

setup(
    name="investment-tracker",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "fastapi>=0.111.0",
        "uvicorn[standard]>=0.29.0",
        "sqlalchemy>=2.0.28",
        "alembic>=1.13.1",
        "pandas>=2.2.1",
        "deltalake>=0.15.3",
    ],
)
