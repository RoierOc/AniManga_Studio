from setuptools import setup, find_packages

setup(
    name="manga-upscaler",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "flask>=3.1.0",
        "opencv-python-headless>=4.8.0",
        "numpy>=1.24.0",
        "pillow>=10.0.0",
        "requests>=2.31.0",
        "spandrel>=0.4.2",
        "torch>=2.11.0",
    ],
    python_requires=">=3.10",
    entry_points={
        "console_scripts": [
            "manga-upscaler=manga_upscaler.cli:main",
        ],
    },
)