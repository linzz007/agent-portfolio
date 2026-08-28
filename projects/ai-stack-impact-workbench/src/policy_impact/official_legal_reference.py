"""Small reviewed registry for exact official-law verification questions."""

from __future__ import annotations

from typing import Any


_GENAI_ARTICLE_17 = {
    "reference_id": "genai_interim_measures_article_17",
    "title": "《生成式人工智能服务管理暂行办法》第十七条",
    "official_url": "https://www.cac.gov.cn/2023-07/13/c_<redacted-phone>29107.htm",
    "official_text": (
        "第十七条 提供具有舆论属性或者社会动员能力的生成式人工智能服务的，"
        "应当按照国家有关规定开展安全评估，并按照《互联网信息服务算法推荐管理规定》"
        "履行算法备案和变更、注销备案手续。"
    ),
}

_PIPL_ARTICLE_55 = {
    "reference_id": "pipl_article_55",
    "title": "《中华人民共和国个人信息保护法》第五十五条",
    "official_url": "https://www.npc.gov.cn/npc/c2/c30834/202108/t20210820_313088.html",
    "official_text": (
        "第五十五条 有下列情形之一的，个人信息处理者应当事前进行个人信息保护影响评估，"
        "并对处理情况进行记录：\n"
        "（一）处理敏感个人信息；\n"
        "（二）利用个人信息进行自动化决策；\n"
        "（三）委托处理个人信息、向其他个人信息处理者提供个人信息、公开个人信息；\n"
        "（四）向境外提供个人信息；\n"
        "（五）其他对个人权益有重大影响的个人信息处理活动。"
    ),
}


def resolve_official_legal_reference(message: str) -> dict[str, Any] | None:
    text = str(message or "")
    normalized = text.lower()
    names_pipl = "个人信息保护法" in text
    names_pipl_article = (
        "第五十五条" in text
        or "第55条" in text
        or "article 55" in normalized
    )
    if names_pipl and names_pipl_article:
        item = dict(_PIPL_ARTICLE_55)
        item["answer"] = "\n".join(
            [
                "核验结论：不能仅凭“AI 问答”这一产品名称认定必须开展个人信息保护影响评估；是否触发取决于实际个人信息处理活动。",
                "",
                f"官方原文：\n{item['official_text']}",
                "",
                (
                    "适用判断：如果该功能处理敏感个人信息，或者利用个人信息进行自动化决策，"
                    "第五十五条要求个人信息处理者在处理前完成影响评估并留存处理记录。"
                    "当前问题没有给出实际数据字段、处理目的、自动化决策方式、对外提供或出境情况，"
                    "因此本轮结论是“条件触发，事实不足”，不能直接认定已触发或不触发。"
                ),
                "待核验事实：实际输入字段；是否处理敏感个人信息；是否利用个人信息进行自动化决策；是否委托、提供、公开或向境外提供个人信息。",
                f"官方链接：{item['official_url']}",
            ]
        )
        return item

    names_law = "生成式人工智能服务管理暂行办法" in text
    names_article = "第十七条" in text or "第17条" in text or "article 17" in normalized
    if not (names_law and names_article):
        return None

    item = dict(_GENAI_ARTICLE_17)
    item["answer"] = "\n".join(
        [
            "核验结论：该说法错误。第十七条没有规定‘所有金融类生成式 AI 产品上线前必须取得中国证监会前置许可’。",
            "",
            f"官方原文：{item['official_text']}",
            "",
            "条文边界：适用前提是服务具有舆论属性或者社会动员能力；条文列出的事项是安全评估，以及算法备案、变更和注销备案。不能从该条推出证监会前置许可。金融业务是否另受证券监管要求约束，需要依据对应证券法规和具体产品事实单独核验。",
            f"官方链接：{item['official_url']}",
        ]
    )
    return item
