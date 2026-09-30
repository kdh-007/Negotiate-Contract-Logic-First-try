"""코드 없이 글로 적힌 참가자격 — 이름 판정(업종·직접생산)과 "확인 필요" 칩(실적·현장설명회·인력).

예문은 과거 제안요청서·공고문 원문 그대로다(jiil-past-contracts, 2026-09-30 조사).
"""

import unittest

from nego import qualify
from nego.text_requirements import flag_requirements, name_bundles

HELD = {
    "4442": "산업디자인전문회사(환경디자인분야)",
    "4990": "실내건축공사업",
    "6010989901": "실물모형및전시물",
    "6012100201": "조형물",
}
LOOKUP = {**HELD, "0002": "건축공사업", "4440": "산업디자인전문회사(시각디자인분야)"}


def judged(item, held=HELD):
    return [[(r.label, r.held) for r in b] for b in name_bundles(item, held, {**LOOKUP, **held})]


class TestNameJudgement(unittest.TestCase):
    def test_design_field_list_is_one_or_requirement(self):
        item = ("③「산업디자인진흥법」제9조의 규정에 의한 산업디자인 전문회사(시각디자인, 제품디자인, 종합디자인 분야 중 "
                "하나 이상)로 신고를 필한 업체")
        b = judged(item)
        self.assertEqual(len(b), 1)
        self.assertEqual([l for l, _ in b[0]], ["산업디자인전문회사(시각디자인분야)", "산업디자인전문회사(제품디자인분야)",
                                                 "산업디자인전문회사(종합디자인분야)"])
        self.assertFalse(any(h for _, h in b[0]), "지일 보유 목록(테스트)엔 환경디자인만 있음")

    def test_design_field_held(self):
        b = judged("가. 산업디자인 전문회사(환경 또는 종합디자인 분야)로 신고를 필한 업체")
        self.assertTrue(any(h for _, h in b[0]))

    def test_direct_production_by_name(self):
        b = judged("1) 「중소기업진흥에 관한 법률」의 규정에 의한 중소기업자로서 직접생산증명서(실물모형)을 소지한 업체")
        self.assertEqual(b, [[("실물모형", True)]], "보유 '실물모형및전시물'이 '실물모형'으로 시작")

    def test_direct_production_by_8_digit_class(self):
        item = "2) … 직접 생산증명서[조각(조형물)<물품번호 : 60121002>]를 소지한 업체"
        self.assertEqual(judged(item), [[("조형물(60121002)", True)]], "보유 6012100201의 앞 8자리")

    def test_numbered_subitems_are_all_required(self):
        item = ("가. 다음 각 조건을 모두 갖춘 업체 1) 산업디자인 전문회사(환경디자인 또는 종합디자인 분야)로 신고를 필한 업체 "
                "2) 「건설산업기본법」에 의한 실내건축공사업을 등록한 업체")
        self.assertEqual(len(judged(item)), 2, "하위 1)·2)는 따로 — 모두 필요")

    def test_longest_name_wins(self):
        self.assertEqual(judged("실내건축공사업으로 등록한 업체"), [[("실내건축공사업(4990)", True)]], "건축공사업을 따로 세지 않음")

    def test_unknown_or_non_requirement_text_is_not_judged(self):
        self.assertEqual(judged("건축사사무소를 개설한 자"), [], "사전에 없는 이름은 판정하지 않음")
        self.assertEqual(judged("공동수급체 대표사는 산업디자인전문회사(환경디자인분야)로 한다"), [])

    def test_attachment_evaluation_uses_names(self):
        items = ["산업디자인 전문회사(시각디자인 분야)로 신고를 필한 업체", "실내건축공사업(업종코드 4990)을 등록한 업체"]
        result = qualify.evaluate_attachment_text(items, set(HELD), HELD, LOOKUP)
        self.assertEqual([g.allowed_names for g in result.missing_groups], [["산업디자인전문회사(시각디자인분야)"]])


class TestFlags(unittest.TestCase):
    def test_track_record(self):
        items = ["8) 입찰공고일 기준으로 최근 3년 이내에 … 전시장의 제작·설치 실적이 단일 건으로 10억원 이상 준공실적이 있는 업체"]
        self.assertEqual([f["kind"] for f in flag_requirements(items)], ["실적"])

    def test_site_briefing_mandatory_with_date(self):
        items = ["5) 사업 현장 설명회에 참가한 업체(불참자는 입찰에 참여 할 수 없음)"]
        full = "나. 현장설명회 : 2026. 10. 7.(수) 14:00, 완도군청 소회의실"
        flags = flag_requirements(items, full)
        self.assertEqual(flags[0]["kind"], "현장설명회")
        self.assertEqual(flags[0]["date"], "2026-10-07 14:00")

    def test_site_briefing_not_mandatory_is_ignored(self):
        for s in ["현장설명회에 참석하지 않은 업체의 질의는 응답하지 않는다.", "별도 현장설명회 없으나 개별 현장실사 필수",
                  "가. 현장설명회 : 별도의 현장설명회는 없습니다."]:
            self.assertEqual(flag_requirements([s], s), [], s)

    def test_architect_office_and_staff(self):
        self.assertEqual([f["kind"] for f in flag_requirements(
            ["5) 「건축사법」 제7조의 규정에 의한 건축사 면허를 소지하고 건축사사무소를 개설하여 업무신고를 필한 자"])], ["건축사사무소"])
        self.assertEqual([f["kind"] for f in flag_requirements(["6) 기본 및 실시설계를 담당하는 자는 건축사 자격증을 보유한 자"])], ["인력"])


if __name__ == "__main__":
    unittest.main()
