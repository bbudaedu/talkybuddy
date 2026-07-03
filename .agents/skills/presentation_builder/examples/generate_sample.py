import sys
import os

# Ensure we can import ppt_template by adding its path to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'scripts')))

from ppt_template import PresentationBuilder

def main():
    print("Initializing PresentationBuilder with 'navy' theme...")
    builder = PresentationBuilder(theme='navy')
    
    # 1. Title Slide
    builder.add_title_slide(
        title="說說學伴人本邊緣AI",
        subtitle="突破高齡數位落差的無障礙語音互動系統"
    )
    
    # 2. Section Divider
    builder.add_section_divider(
        title="第一部分：核心痛點與挑戰",
        description="探討高齡與弱勢族群在使用現代科技時面臨的真實摩擦"
    )
    
    # 3. Content Slide: Cognitive Friction
    builder.add_content_slide(
        title="高齡族群的數位摩擦",
        points=[
            "認知摩擦：複雜的選單、過小的字體與抽象圖示造成操作困難",
            "網路摩擦：偏鄉或室內網路不穩定，使得雲端語音助理經常無法回應",
            "語言摩擦：現有主流語音助理對台語、客語或地方腔調的辨識度極低",
            "心理摩擦：害怕操作錯誤導致設備損壞，產生科技排斥感"
        ]
    )
    
    # 4. Section Divider
    builder.add_section_divider(
        title="第二部分：解決方案與核心技術",
        description="運用本機邊緣運算與客製化語音模型打造極簡互動體驗"
    )
    
    # 5. Split Slide: Comparison
    builder.add_split_slide(
        title="邊緣 AI 語音助理的架構優勢",
        left_title="傳統雲端語音助理",
        left_points=[
            "高延遲：語音數據往返雲端需數秒時間",
            "依賴連線：無網路訊號時完全無法運作",
            "隱私風險：家庭私密對話上傳至雲端伺服器",
            "通用模型：無法針對地方腔調進行局部優化"
        ],
        right_title="說說學伴邊緣 AI",
        right_points=[
            "零延遲：本機晶片微秒級即時回應",
            "100% 離線：不需網路即可流暢語音控制",
            "隱私安全：所有語音資料皆在裝置內銷毀",
            "客製化模型：專為高齡語音與台語腔調優化"
        ]
    )
    
    # 6. Metric Slide
    builder.add_metric_slide(
        title="關鍵效能指標與成效",
        metric_number="100%",
        metric_label="本機離線隱私保護與零連線依賴",
        description_points=[
            "完全去中心化：所有語音特徵提取與意圖識別皆在邊緣端完成",
            "超低反應時間：從語音輸入到控制指令輸出控制在 150 毫秒內",
            "高齡語意容錯：整合模糊語意比對，能聽懂斷續、重複或非標準語句",
            "軟硬整合：可運行於低功耗邊緣運算板卡，實現低成本普惠科技"
        ]
    )
    
    # Output path
    output_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..', 'demo_presentation.pptx'))
    print(f"Saving presentation to {output_path}...")
    builder.save(output_path)
    print("Demo presentation generated successfully.")

if __name__ == "__main__":
    main()
