#!/bin/bash
# Manga Upscaler - waifu2x-ncnn-vulkan
# Usage: ./upscale.sh input.png [output.png]

INPUT="$1"
OUTPUT="$2"
SCALE=2
NOISE=0
MODEL="models-cunet"
GPU=0

if [ -z "$INPUT" ]; then
    echo "Usage: ./upscale.sh input.png [output.png]"
    echo ""
    echo "Examples:"
    echo "  ./upscale.sh manga.png"
    echo "  ./upscale.sh manga.png output.png"
    echo "  ./upscale.sh manga.png -n 1"
    exit 1
fi

if [ -z "$OUTPUT" ]; then
    OUTPUT="${INPUT%.png}_upscaled.png"
fi

echo "=== Manga Upscaler ==="
echo "Input:   $INPUT"
echo "Output:  $OUTPUT"
echo "Scale:   ${SCALE}x"
echo "Noise:   $NOISE"

./waifu2x-ncnn-vulkan \
    -i "$INPUT" \
    -o "$OUTPUT" \
    -s $SCALE \
    -n $NOISE \
    -m $MODEL \
    -g $GPU \
    -f png

echo "Done!"