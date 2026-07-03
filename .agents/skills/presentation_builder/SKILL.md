---
name: presentation_builder
description: Generate and edit professional PowerPoint (.pptx) presentations using high-quality custom layouts, typography, and color schemes.
---

# Presentation Builder Skill

This skill provides guidelines, APIs, and templates to generate professional, well-structured, and visually stunning PowerPoint (`.pptx`) presentations. It enforces clean modern design principles, consistent typography, and curated color palettes.

## Design Principles

1. **Widescreen 16:9 Layout**: All presentations must use widescreen dimensions (13.33 inches width, 7.5 inches height) to match modern displays.
2. **Curated Color Themes**: Avoid default and plain saturated colors. Use one of the following palettes:
   - **Deep Navy & Amber (Professional/Corporate)**:
     - Primary (Background): `#0F172A` (Slate 900) or `#FFFFFF` (White)
     - Text/Accents: `#1E293B` (Slate 800), `#475569` (Slate 600)
     - Accent/Highlight: `#F59E0B` (Amber 500)
   - **Forest Green & Mint (Creative/Eco/Tech)**:
     - Primary: `#064E3B` (Green 900) or `#FFFFFF`
     - Text/Accents: `#0F766E` (Teal 700), `#115E59` (Teal 800)
     - Accent/Highlight: `#10B981` (Emerald 500)
   - **Slate & Coral (Modern/Tech)**:
     - Primary: `#0F172A` or `#FFFFFF`
     - Text/Accents: `#334155` (Slate 700), `#64748B` (Slate 500)
     - Accent/Highlight: `#F43F5E` (Rose 500)
3. **Typography Hierarchy**:
   - Use standard professional fonts. For Chinese/Bilingual slides, use **Microsoft JhengHei** (微軟正黑體) or **Arial**.
   - Title: `36pt` - `44pt` (Bold)
   - Subtitle/Section Title: `20pt` - `24pt`
   - Body Text: `14pt` - `16pt` (Regular)
   - Caption/Footer: `10pt` - `12pt`
4. **Layout Composition**:
   - **Title Slide**: Strong dark background with a large title and subtitle.
   - **Section Divider**: A clean transitional slide with a primary brand background color.
   - **Standard Content Slide**: Clean light background, clear heading, and structured bullet points or columns.
   - **Two-Column Slide**: Split comparison layout with left and right content boxes.
   - **Hero/Big Number Slide**: A layout highlighting a single key metric or big number with a label and explanation.

## How to Use

The presentation builder exposes a helper script at `scripts/ppt_template.py`. You can import the `PresentationBuilder` class in any Python script to programmatically construct slides:

```python
from ppt_template import PresentationBuilder

# Initialize builder with a chosen theme ('navy', 'forest', or 'slate')
builder = PresentationBuilder(theme='navy')

# Add a title slide
builder.add_title_slide(
    title="臺灣普惠科技應用痛點",
    subtitle="用人本邊緣AI與語音互動打破數位落差"
)

# Add a standard content slide
builder.add_content_slide(
    title="核心痛點分析",
    points=[
        "高齡族群操作複雜介面時的認知摩擦",
        "偏鄉網路環境不穩定，需依賴離線邊緣運算",
        "現有語音辨識對台語或地方腔調支援不足"
    ]
)

# Add a split column slide
builder.add_split_slide(
    title="傳統方案 vs. 邊緣AI方案",
    left_title="傳統雲端方案",
    left_points=[
        "高延遲：需將語音傳送至雲端伺服器",
        "隱私風險：敏感語音數據外流",
        "斷網即失效：偏鄉連線品質不佳"
    ],
    right_title="本機邊緣AI方案",
    right_points=[
        "零延遲：本機晶片直接處理語音",
        "隱私保護：語音資料不出裝置",
        "離線可用：完全不依賴網路連線"
    ]
)

# Save the presentation
builder.save("output_presentation.pptx")
```
