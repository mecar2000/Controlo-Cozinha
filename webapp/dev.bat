@echo off
REM Starts Flask backend (:5010) and Vite dev server (:5173) in separate windows.
REM Vite proxies /api to Flask, so open http://localhost:5173 for hot-reload dev mode.
start "backend" cmd /k python server.py
start "frontend" cmd /k npm run dev --prefix frontend
