# backend/app/scenarios/brainstorming.py

scenario_config = {
    "name": "腦力激盪",
    "description": "鼓勵創新思考和多元想法的生成，不設限制地探索各種可能性",
    "system_prompt": "這是一個腦力激盪情境。請自由發揮你的想像力，不要擔心想法是否實用或可行。所有想法都是有價值的，請積極建立在他人想法之上，推陳出新。避免過早批評他人的創意。",
    "round_structure": {
        1: "自由發想：開放式地提出各種想法和可能性，不受限制",
        2: "擴展延伸：在已有想法的基礎上進行延伸和拓展，產生更多創意",
        3: "組合整合：將不同的想法進行組合和整合，形成更完整的方案",
        4: "評估篩選：對所有想法進行初步評估，篩選出最有潛力的方向"
    },
    "role_emphasis": {
        "General manager": 0.9,       # 稍微降低領導角色的發言權重
        "Business manager": 1.0,       # 保持正常權重
        "Marketing manager": 1.8,     # 顯著增強行銷角色的發言權重
        "Financial manager": 0.8,  # 稍微降低財務角色的發言權重
        "R&D director": 1.5,   # 增強技術角色的發言權重
        "HR": 1.0    # 保持正常權重
    },
    "discussion_guidance": "在腦力激盪情境中，沒有所謂'壞'的想法，所有參與者應當自由表達創意，不要急於批評或評判他人的想法，而是嘗試在他人想法的基礎上進一步發展。"
} 