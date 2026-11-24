# Vì sao v3b fail: phân tích 24 task (21/11/2026)

Nguồn:
- `results/lora_eval24/v3b_lora_seq2_per_task.jsonl` và các trace trong `results/lora_eval24/eval_v3b_lora_seq2/traces/`;
- prompt và `eval_config.yaml` trong `results/lora_eval24/sub_v3b_lora_seq2/`;
- dataset cộng đồng `data/task_soundness/` (dữ liệu bên ngoài; mình đối chiếu, không coi là sự thật tuyệt đối).

Nhãn: SỰ THẬT = đếm trực tiếp từ file; GIẢ THUYẾT = suy ra, chưa thử.

## Tóm tắt

v3b giải được 3/24 task. Trong 21 task fail: 19 có patch nhưng sai, 2 không có patch. Bốn điều sau, xếp theo mức chắc chắn:

| # | Phát hiện | Bằng chứng | Mức |
|---|---|---|---|
| 1 | **Bộ 24 task có 11 task chưa cấu hình nào giải được** | `task_discrimination.csv`, nhóm "never resolved", 8 cấu hình | SỰ THẬT (theo dataset) |
| 2 | **43% lần `edit_file` thất bại** (35/81), chủ yếu "old_string not found" | đếm trong trace | SỰ THẬT |
| 3 | **9/24 task chạm hoặc gần chạm trần 36 lần gọi tool**, trong khi thời gian chỉ dùng trung vị 73/480 s | `per_task.jsonl`, `eval_config.yaml` | SỰ THẬT |
| 4 | **Agent gần như không chạy test của repo** (pytest chỉ ở 1/24 task) | trace; `coder.md` ghi "NEVER run bare `pytest`" | SỰ THẬT. Còn chuyện chạy test có giúp được hay không thì là GIẢ THUYẾT. |

## Chi tiết

### 1. Trần thật của bộ 24 task là khoảng 13, không phải 24

Có 11 task không cấu hình nào trong dataset giải được, kể cả các cấu hình 31B. 13 task còn lại là các task "phân biệt cấu hình". Cả 24 task đều sound: code gốc fail test, bản sửa tham chiếu pass.

Hệ quả: so 2/24 với 3/24 gần như toàn là nhiễu. Thêm bằng chứng cho điều này: E4B zero-thinking trong dataset cũng giải được 3/24 trên đúng 24 task này, nhưng là một bộ 3 task khác (fastapi_14873, requests_7315, requests_7427). Hai bộ chỉ trùng nhau 1 task.

### 2. Lỗi sửa file

- 35 trên 81 lần `edit_file`/`write_file` báo lỗi.
- `rich_3063`: cả 9 lần sửa đều lỗi, nên patch rỗng.
- `fastapi_14360`: agent sửa được một lần, sau đó mất 3 lần gọi liên tiếp vì "old_string not found", rồi tự xoá luôn dòng vừa sửa.
- Có 6 lần agent viết script `python3 -c "..."` với dấu nháy lồng nhau. Bash báo `unexpected EOF`, nên các lần gọi đó chỉ tốn ngân sách.

GIẢ THUYẾT: model viết sẵn `\n` và `\"` bên trong `old_string`. Harness có thử bản bỏ escape (`"strategy": "unescaped"`), nhưng không phải lúc nào cũng khớp.

### 3. Hết ngân sách tool call, không phải hết thời gian

- 9 task dùng từ 35/36 lần gọi trở lên.
- Thời gian agent có trung vị 73 s, trong khi trần là 480 s.
- Thời gian thật trung bình mỗi task là 113 s. Nếu bộ đề ẩn có 120 task chạy lần lượt, tổng là khoảng 3,8 giờ trên 12 giờ (GIẢ THUYẾT: chưa biết kích thước bộ ẩn, cũng chưa biết task có chạy lần lượt không).

Trần 36 do mình tự đặt. Các cấu hình trong dataset dùng 50-100 lần gọi.

### 4. Kiểm tra bằng script tự chế thay vì test của repo

`coder.md` cấm "bare `pytest`", còn `verifier.md` chỉ cho một lần pytest có `-k`. Agent thường viết lại logic thành bản giả (mock) rồi assert lên bản giả đó. Ví dụ ở `fastapi_14360`, agent mô phỏng lại `request_params_to_args`. Cách này không kiểm tra code thật.

Theo README của harness, pytest dùng được trong sandbox (`enable_sandbox_testing = True`).

## Kết quả trên leaderboard để so

Theo `leaderboard_observations.csv`, các cấu hình công khai đạt 0,06-0,22. Cùng một archive ("Rozen reference") lúc được 0,12, lúc 0,10, lúc 0,06. Vậy riêng leaderboard đã dao động khoảng ±0,05. v0 được 0,00, thấp hơn mọi mốc đó.

## Nên thử gì tiếp, theo thứ tự

1. **Tăng `max_tool_calls` từ 36 lên khoảng 60.** Rẻ nhất, chỉ sửa 1 dòng config. Thời gian còn dư nhiều.
2. **Sửa file chắc hơn:**
   - Trong prompt, yêu cầu chép `old_string` nguyên văn từ output `sed -n` mới nhất, không tự thêm escape.
   - Chuyển sang cách thay bằng script Python sau 1 lần lỗi thay vì 2.
   - Cấm `python3 -c` dài nhiều dòng; dùng heredoc `python3 - <<'EOF'`.
3. **Cho phép chạy file test có sẵn của module vừa sửa** (`pytest tests/test_x.py -x -q`), thay vì bản giả tự viết. GIẢ THUYẾT: lợi lớn nhất nằm ở đây, nhưng tốn thời gian hơn.

Cách đo: chỉ so trên 13 task "phân biệt cấu hình", mỗi biến thể chạy 2 seed, và so theo từng cặp task (task nào A giải được mà B không). Không so tổng điểm trên 24 task.
