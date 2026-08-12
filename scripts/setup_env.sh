#!/bin/bash
# XHS Note Generator - Environment Setup Script

set -e

echo "🚀 Setting up XHS Note Generator development environment..."

# Check Python version
PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
echo "📍 Python version: $PYTHON_VERSION"

# Create virtual environment if not exists
if [ ! -d ".venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv .venv
fi

# Activate virtual environment
echo "🔧 Activating virtual environment..."
source .venv/bin/activate

# Upgrade pip
echo "⬆️  Upgrading pip..."
pip install --upgrade pip

# Install requirements
echo "📥 Installing dependencies..."
pip install -r requirements.txt

# Create data directory
echo "📁 Creating data directories..."
mkdir -p data/chroma

# Copy .env.example if .env not exists
if [ ! -f ".env" ]; then
    echo "📝 Creating .env file from template..."
    cp .env.example .env
    echo "⚠️  Please edit .env file and add your API keys!"
fi

# Verify imports
echo "🔍 Verifying imports..."
python3 -c "import app; print('✅ App module imported successfully')"

echo ""
echo "✨ Setup complete!"
echo ""
echo "Next steps:"
echo "1. Edit .env file with your API keys"
echo "2. source .venv/bin/activate"
echo "3. cd third_party/Spider_XHS && pip install -r requirements.txt"
echo "4. Run: uvicorn app.main:app --reload"
