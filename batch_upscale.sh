#!/bin/bash
# Batch process all images in a folder
# Usage: ./batch_upscale.sh input_folder output_folder

INPUT_DIR="${1:-samples}"
OUTPUT_DIR="${2:-output}"

mkdir -p "$OUTPUT_DIR"

echo "=== Batch Upscale ==="
echo "Input:  $INPUT_DIR"
echo "Output: $OUTPUT_DIR"

for img in "$INPUT_DIR"/*.png; do
    if [ -f "$img" ]; then
        filename=$(basename "$img")
        echo "Processing: $filename"
        
        ./waifu2x-ncnn-vulkan \
            -i "$img" \
            -o "$OUTPUT_DIR/${filename%.png}_waifu2x.png" \
            -s 2 \
            -n 0 \
            -m models-cunet \
            -g 0 \
            -f png
    fi
done

echo ""
echo "Done! Check: $OUTPUT_DIR/"