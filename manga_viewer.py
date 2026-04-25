#!/usr/bin/env python3
"""
Simple Manga Viewer
Usage: python manga_viewer.py folder
"""

import os
import sys
import tkinter as tk
from tkinter import filedialog
from PIL import Image, ImageTk
from pathlib import Path

class MangaViewer:
    def __init__(self, folder=None):
        self.root = tk.Tk()
        self.root.title("Manga Viewer")
        self.root.geometry("800x1200")
        
        self.folder = folder
        self.image_files = []
        self.current_index = 0
        self.zoom = 1.0
        
        # UI
        self.label = tk.Label(self.root)
        self.label.pack(fill=tk.BOTH, expand=tk.YES)
        
        # Controls
        control_frame = tk.Frame(self.root)
        control_frame.pack(side=tk.BOTTOM, fill=tk.X)
        
        tk.Button(control_frame, text="Open Folder", command=self.open_folder).pack(side=tk.LEFT, padx=5, pady=5)
        tk.Button(control_frame, text="< Prev", command=self.prev).pack(side=tk.LEFT, padx=5, pady=5)
        tk.Button(control_frame, text="Next >", command=self.next).pack(side=tk.LEFT, padx=5, pady=5)
        tk.Button(control_frame, text="+", command=self.zoom_in).pack(side=tk.LEFT, padx=5, pady=5)
        tk.Button(control_frame, text="-", command=self.zoom_out).pack(side=tk.LEFT, padx=5, pady=5)
        
        self.info_label = tk.Label(control_frame, text="")
        self.info_label.pack(side=tk.RIGHT, padx=10)
        
        # Bind keys
        self.root.bind('<Left>', lambda e: self.prev())
        self.root.bind('<Right>', lambda e: self.next())
        self.root.bind('+', lambda e: self.zoom_in())
        self.root.bind('-', lambda e: self.zoom_out())
        self.root.bind('<space>', lambda e: self.next())
        
        if folder:
            self.load_folder(folder)
        
        self.root.mainloop()
    
    def open_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.load_folder(folder)
    
    def load_folder(self, folder):
        self.folder = folder
        extensions = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif'}
        self.image_files = sorted([f for f in Path(folder).iterdir() if f.suffix.lower() in extensions])
        
        if self.image_files:
            self.current_index = 0
            self.show_image()
            print(f"Loaded {len(self.image_files)} images from {folder}")
    
    def show_image(self):
        if not self.image_files:
            return
        
        img_path = self.image_files[self.current_index]
        
        try:
            img = Image.open(img_path)
            
            # Resize for display
            w, h = img.size
            max_w = self.root.winfo_width() or 800
            max_h = self.root.winfo_height() - 100 or 1100
            
            scale = min(max_w / w, max_h / h) * self.zoom
            new_size = (int(w * scale), int(h * scale))
            
            img_display = img.resize(new_size, Image.LANCZOS)
            self.photo = ImageTk.PhotoImage(img_display)
            
            self.label.config(image=self.photo, text="")
            self.info_label.config(text=f"{self.current_index + 1}/{len(self.image_files)} - {img_path.name}")
            
        except Exception as e:
            self.label.config(text=f"Error: {e}")
    
    def prev(self):
        if self.current_index > 0:
            self.current_index -= 1
            self.show_image()
    
    def next(self):
        if self.current_index < len(self.image_files) - 1:
            self.current_index += 1
            self.show_image()
    
    def zoom_in(self):
        self.zoom = min(self.zoom * 1.2, 3.0)
        self.show_image()
    
    def zoom_out(self):
        self.zoom = max(self.zoom / 1.2, 0.3)
        self.show_image()

if __name__ == "__main__":
    folder = sys.argv[1] if len(sys.argv) > 1 else None
    MangaViewer(folder)
