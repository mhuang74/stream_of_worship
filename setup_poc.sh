#!/bin/bash
set -e

echo "============================================================================"
echo "Setting up Worship Music System - POC Environment"
echo "============================================================================"
echo ""

# Check for Docker
echo "Checking prerequisites..."
if ! command -v docker &> /dev/null; then
    echo "❌ ERROR: Docker not found. Please install Docker Desktop."
    echo "   Download: https://www.docker.com/products/docker-desktop/"
    exit 1
fi
echo "✓ Docker found: $(docker --version)"

if ! command -v docker-compose &> /dev/null; then
    echo "❌ ERROR: Docker Compose not found. Please install Docker Compose."
    echo "   Download: https://docs.docker.com/compose/install/"
    exit 1
fi
echo "✓ Docker Compose found: $(docker-compose --version)"

# Check if Docker daemon is running
if ! docker info &> /dev/null; then
    echo "❌ ERROR: Docker daemon is not running."
    echo "   Please start Docker Desktop and wait for it to be ready."
    exit 1
fi
echo "✓ Docker daemon is running"
echo ""

# Create directories
echo "Creating project directories..."
mkdir -p notebooks poc_audio poc_output data
touch notebooks/.gitkeep poc_audio/.gitkeep poc_output/.gitkeep data/.gitkeep
echo "✓ Directories created: notebooks/, poc_audio/, poc_output/, data/"
echo ""

# Check for audio files
echo "Checking for audio files..."
audio_count=$(find poc_audio -name "*.mp3" -o -name "*.flac" | wc -l | tr -d ' ')
if [ "$audio_count" -eq 0 ]; then
    echo "⚠️  WARNING: No audio files found in poc_audio/"
    echo "   Please add 3-5 worship songs (MP3/FLAC) to poc_audio/ directory"
    echo ""
else
    echo "✓ Found $audio_count audio file(s) in poc_audio/"
    echo ""
fi

# Build Docker image
echo "Building Docker image (this may take several minutes)..."
echo "============================================================================"
docker-compose build
echo "============================================================================"
echo "✓ Docker image built successfully"
echo ""

# Summary
echo "============================================================================"
echo "Setup Complete!"
echo "============================================================================"
echo ""
echo "Next steps:"
echo ""
if [ "$audio_count" -eq 0 ]; then
    echo "1. Place 3-5 audio files (MP3/FLAC) in poc_audio/ directory"
    echo "   Example: cp /path/to/your/songs/*.mp3 poc_audio/"
    echo ""
    echo "2. Run the POC analysis (choose one method):"
else
    echo "1. Run the POC analysis (choose one method):"
fi
echo ""
echo "   OPTION A: Command-Line Script (Recommended)"
echo "   ----------------------------------------"
echo "   docker-compose run --rm jupyter python poc/poc_analysis.py"
echo ""
echo "   OPTION B: Interactive Jupyter Notebook"
echo "   ----------------------------------------"
echo "   docker-compose up"
echo "   # Then open browser to: http://localhost:8888"
echo "   # Navigate to: notebooks/01_POC_Analysis.ipynb"
echo "   # Run all cells: Menu → Run → Run All Cells"
echo ""
if [ "$audio_count" -eq 0 ]; then
    echo "3. Review outputs in poc_output/ directory"
else
    echo "2. Review outputs in poc_output/ directory"
fi
echo ""
echo "============================================================================"
echo "Documentation: README.md"
echo "POC Script Guide: poc/README.md"
echo "Design Document: specs/worship-music-transition-system-design.md"
echo "============================================================================"
