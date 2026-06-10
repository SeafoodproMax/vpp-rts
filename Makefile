# VPP-RTS 便利指令
#   make clean      刪除 output/ 內所有產生的檔案（保留 .gitkeep）
.PHONY: clean clean-tex clean-all

clean:
	find output -type f ! -name '.gitkeep' -delete
	@echo "Cleaned output/ (kept .gitkeep)"
