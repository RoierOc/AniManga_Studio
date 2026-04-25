#!/usr/bin/env python3
"""
Manga Pipeline UI - Real-time manga management with upscaling
"""

import os
import sys
import threading
import queue
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from PIL import Image, ImageTk

MANGA_DIR = os.path.expanduser("~/MangaLibrary")
UPSCALED_DIR = os.path.expanduser("~/MangaLibrary_Upscaled")
LOG_FILE = "/tmp/manga_pipeline.log"

class MangaPipelineUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Manga Pipeline")
        self.root.geometry("900x700")
        
        self.queue = queue.Queue()
        self.processing = False
        self.current_manga = None
        
        self.setup_ui()
        self.load_library()
        
    def setup_ui(self):
        # Header
        header = tk.Frame(self.root, bg="#2c3e50", height=60)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        
        tk.Label(header, text="Manga Pipeline", fg="white", bg="#2c3e50").pack(pady=15)
        
        # Main content - split view
        main = tk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        main.pack(fill=tk.BOTH, expand=True)
        
        # Left panel - Library
        left = tk.Frame(main, bg="#ecf0f1")
        main.add(left, width=300)
        
        tk.Label(left, text="Library", bg="#ecf0f1").pack(pady=5)
        
        # Add manga button
        btn_frame = tk.Frame(left, bg="#ecf0f1")
        btn_frame.pack(fill=tk.X, padx=5, pady=5)
        
        tk.Button(btn_frame, text="+ Add Manga", command=self.add_manga,
                bg="#3498db", fg="white").pack(side=tk.LEFT, padx=2)
        tk.Button(btn_frame, text="Refresh", command=self.load_library,
                bg="#95a5a6", fg="white").pack(side=tk.LEFT, padx=2)
        
        # Manga list
        list_frame = tk.Frame(left, bg="#ecf0f1")
        list_frame.pack(fill=tk.BOTH, expand=True, padx=5)
        
        self.manga_listbox = tk.Listbox(list_frame)
        self.manga_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.manga_listbox.bind('<<ListboxSelect>>', self.on_manga_select)
        
        scroll = tk.Scrollbar(list_frame, command=self.manga_listbox.yview)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.manga_listbox.config(yscrollcommand=scroll.set)
        
        # Right panel - Details & Progress
        right = tk.Frame(main, bg="white")
        main.add(right, width=500)
        
        # Manga title
        self.title_label = tk.Label(right, text="Select a manga", bg="white")
        self.title_label.pack(pady=10)
        
        # URL input
        url_frame = tk.Frame(right, bg="white")
        url_frame.pack(fill=tk.X, padx=20, pady=5)
        
        tk.Label(url_frame, text="MangaDex URL/ID:", bg="white").pack(side=tk.LEFT)
        self.url_entry = tk.Entry(url_frame)
        self.url_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        tk.Button(url_frame, text="Add", command=self.add_manga_from_url,
                 bg="#27ae60", fg="white").pack(side=tk.LEFT)
        
        # Chapter info
        self.chapter_label = tk.Label(right, text="", bg="white")
        self.chapter_label.pack(pady=5)
        
        # Progress section
        prog_frame = tk.Frame(right, bg="white")
        prog_frame.pack(fill=tk.X, padx=20, pady=10)
        
        self.progress = ttk.Progressbar(prog_frame, length=400, mode='determinate')
        self.progress.pack(pady=5)
        
        self.status_label = tk.Label(right, text="Ready", bg="white", fg="#7f8c8d")
        self.status_label.pack(pady=5)
        
        # Action buttons
        act_frame = tk.Frame(right, bg="white")
        act_frame.pack(pady=10)
        
        tk.Button(act_frame, text="Download", command=self.download_manga,
                bg="#3498db", fg="white", width=12).pack(side=tk.LEFT, padx=5)
        tk.Button(act_frame, text="Upscale", command=self.upscale_manga,
                bg="#e74c3c", fg="white", width=12).pack(side=tk.LEFT, padx=5)
        
        # Log
        log_frame = tk.Frame(right, bg="white")
        log_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)
        
        tk.Label(log_frame, text="Log:", bg="white").pack(anchor=tk.W)
        
        self.log_text = tk.Text(log_frame, height=15)
        self.log_text.pack(fill=tk.BOTH, expand=True)
        
        scroll_log = tk.Scrollbar(log_frame, command=self.log_text.yview)
        scroll_log.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.config(yscrollcommand=scroll_log.set)
        
    def log(self, msg):
        """Add message to log"""
        timestr = time.strftime("%H:%M:%S")
        self.log_text.insert(tk.END, f"[{timestr}] {msg}\n")
        self.log_text.see(tk.END)
        
    def load_library(self):
        """Load manga library"""
        self.manga_listbox.delete(0, tk.END)
        
        os.makedirs(MANGA_DIR, exist_ok=True)
        
        for folder in sorted(Path(MANGA_DIR).iterdir()):
            if folder.is_dir():
                images = list(folder.glob('*.png')) + list(folder.glob('*.jpg'))
                self.manga_listbox.insert(tk.END, f"{folder.name} ({len(images)}p)")
    
    def add_manga(self):
        """Add manga via dialog"""
        url = filedialog.askopenfilename(title="Select manga folder or enter URL")
        if url:
            self.url_entry.delete(0, tk.END)
            self.url_entry.insert(0, url)
    
    def add_manga_from_url(self):
        """Add manga from URL"""
        url = self.url_entry.get().strip()
        if not url:
            messagebox.showwarning("Warning", "Enter MangaDex URL or ID")
            return
        
        name = url.split('/')[-1] or url.split('/')[-2]
        folder = os.path.join(MANGA_DIR, name)
        os.makedirs(folder, exist_ok=True)
        
        self.log(f"Added: {name}")
        self.url_entry.delete(0, tk.END)
        self.load_library()
    
    def on_manga_select(self, event):
        """Manga selected"""
        selection = self.manga_listbox.curselection()
        if selection:
            text = self.manga_listbox.get(selection[0])
            name = text.split(' (')[0]
            self.current_manga = name
            self.title_label.config(text=name)
            
            # Get chapter count
            folder = os.path.join(MANGA_DIR, name)
            if os.path.exists(folder):
                images = list(Path(folder).glob('*.png')) + list(Path(folder).glob('*.jpg'))
                self.chapter_label.config(text=f"{len(images)} pages")
            else:
                self.chapter_label.config(text="Not downloaded")
    
    def download_manga(self):
        """Download manga from MangaDex"""
        if not self.current_manga:
            messagebox.showwarning("Warning", "Select a manga first")
            return
        
        self.log(f"Downloading {self.current_manga}...")
        self.status_label.config(text="Downloading...")
        self.progress.config(mode='indeterminate')
        self.progress.start()
        
        # In background
        threading.Thread(target=self._download_thread, daemon=True).start()
    
    def _download_thread(self):
        import subprocess
        try:
            folder = os.path.join(MANGA_DIR, self.current_manga)
            cmd = ['mangadex-dl', self.current_manga, '-o', folder]
            subprocess.run(cmd)
            
            self.queue.put(('done_download', self.current_manga))
        except Exception as e:
            self.queue.put(('error', str(e)))
    
    def upscale_manga(self):
        """Upscale manga with 4x model"""
        if not self.current_manga:
            messagebox.showwarning("Warning", "Select a manga first")
            return
        
        self.log(f"Upscaling {self.current_manga} with 4x...")
        self.status_label.config(text="Upscaling...")
        
        # In background
        threading.Thread(target=self._upscale_thread, daemon=True).start()
    
    def _upscale_thread(self):
        import torch
        from spandrel import ModelLoader
        
        model_path = '/root/workspace/MangaJaNai/models/4x_IllustrationJaNai_V2standard_FDAT_M_52k.safetensors'
        
        input_folder = os.path.join(MANGA_DIR, self.current_manga)
        output_folder = os.path.join(UPSCALED_DIR, self.current_manga)
        os.makedirs(output_folder, exist_ok=True)
        
        try:
            model_desc = ModelLoader().load_from_file(model_path)
            model = model_desc.model.cuda().eval()
            
            images = sorted(Path(input_folder).glob('*.png')) + sorted(Path(input_folder).glob('*.jpg'))
            total = len(images)
            
            for i, img_path in enumerate(images):
                self.queue.put(('progress', i, total, img_path.name))
                
                img = Image.open(img_path)
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                
                import numpy as np
                img_np = np.array(img).astype(np.float32) / 255.0
                img_tensor = torch.from_numpy(img_np).permute(2,0,1).unsqueeze(0).cuda()
                
                with torch.no_grad():
                    out = model(img_tensor)
                
                out_np = out.squeeze(0).permute(1,2,0).cpu().numpy()
                out_np = (out_np * 255).clip(0,255).astype(np.uint8)
                
                out_img = Image.fromarray(out_np)
                out_path = os.path.join(output_folder, img_path.name)
                out_img.save(out_path, quality=95)
            
            self.queue.put(('done_upscale', self.current_manga, total))
            
        except Exception as e:
            self.queue.put(('error', str(e)))
    
    def update(self):
        """Update UI from queue"""
        try:
            while True:
                msg = self.queue.get_nowait()
                
                if msg[0] == 'progress':
                    i, total, name = msg[1], msg[2], msg[3]
                    pct = (i / total) * 100
                    self.progress.config(mode='determinate', value=pct)
                    self.status_label.config(text=f"Processing {i}/{total}: {name}")
                    self.log(f"Upscaling: {name}")
                    
                elif msg[0] == 'done_download':
                    self.log(f"Download complete: {msg[1]}")
                    self.status_label.config(text="Done")
                    self.progress.stop()
                    
                elif msg[0] == 'done_upscale':
                    self.log(f"Upscale complete: {msg[1]} ({msg[2]} pages)")
                    self.status_label.config(text="Done")
                    self.load_library()
                    
                elif msg[0] == 'error':
                    self.log(f"ERROR: {msg[1]}")
                    self.status_label.config(text="Error")
                    
        except queue.Empty:
            pass
        
        self.root.after(100, self.update)

def main():
    app = MangaPipelineUI()
    app.root.after(100, app.update)
    app.root.mainloop()

if __name__ == "__main__":
    main()