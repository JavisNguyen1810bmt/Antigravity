import pymupdf as fitz
from pathlib import Path

def extract_pages_and_images(input_pdf: Path, start_page: int, end_page: int, pictures_dir: Path):
    extracted_text = ""
    images_found = []

    doc = fitz.open(input_pdf)
    actual_end = min(end_page, len(doc))

    for page_num in range(start_page, actual_end):
        page = doc[page_num]
        extracted_text += page.get_text() + "\n"

        image_list = page.get_images(full=True)
        for img_idx, img in enumerate(image_list):
            xref = img[0]
            base_image = doc.extract_image(xref)
            image_bytes = base_image["image"]
            image_ext = base_image["ext"]

            if len(image_bytes) > 10240:
                filename = f"p{page_num}_img{img_idx}.{image_ext}"
                filepath = pictures_dir / filename
                with open(filepath, "wb") as f:
                    f.write(image_bytes)
                images_found.append(filename)
    doc.close()

    if images_found:
        meta_instruction = "\n[LƯU Ý CHO AI: TRÊN CÁC TRANG NÀY CÓ CHỨA CÁC HÌNH ẢNH SAU: "
        meta_instruction += ", ".join(images_found)
        meta_instruction += ". HÃY TỰ ĐỘNG CHÈN LỆNH \\includegraphics[max width=\\linewidth]{Pictures/tên_ảnh} VÀO VỊ TRÍ THÍCH HỢP TRONG BẢN DỊCH ĐỂ TRÌNH BÀY LẠI HÌNH ẢNH!]\n\n"
        extracted_text = meta_instruction + extracted_text

    return extracted_text