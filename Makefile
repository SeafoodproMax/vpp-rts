# VPP-RTS 便利指令
#   make clean      刪除 output/ 內所有產生的檔案（保留 .gitkeep）
#   make clean-tex  刪除報告的 LaTeX 暫存檔
#   make clean-all  output + LaTeX 暫存 + __pycache__ 全部清掉
.PHONY: clean clean-tex clean-all

clean:
	find output -type f ! -name '.gitkeep' -delete
	@echo "Cleaned output/ (kept .gitkeep)"

clean-tex:
	rm -f 報告_VPP-RTS.aux 報告_VPP-RTS.log 報告_VPP-RTS.out 報告_VPP-RTS.toc
	@echo "Cleaned LaTeX aux files"

clean-all: clean clean-tex
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	@echo "Cleaned __pycache__"
