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
        self.assertEqual(judged("사. 적격심사 평가대상 업종 및 평가비율\n종합 건축공사업 680,362,000 100%"), [])

    def test_name_only_subitem_inside_coded_item(self):
        """2026-10-01 하남역사박물관: 코드 있는 하위 항목 옆 "4) …에 따른 소프트웨어사업자"가 빠졌었다.
        분야 없는 통칭은 분야 중 하나를 보유하면 충족."""
        held = {**HELD, "1469": "소프트웨어사업자(디지털콘텐츠개발서비스사업)"}
        lookup = {**LOOKUP, "1426": "소프트웨어사업자", "1469": "소프트웨어사업자"}
        item = ("나. 나라장터(G2B)에 아래 자격을 모두 갖춘 업체\n"
                "   1) 직접생산확인증명서[실물모형및전시물, 세부품명번호: 6010989901]를 소지한 업체\n"
                "   2) 산업디자인전문회사[업종코드 4442] 로 등록되어 있는 업체\n"
                "   3) ｢소프트웨어산업 진흥법｣ 제24조(소프트웨어사업자의 신고)에 따른 소프트웨어사업자")
        ok = qualify.evaluate_attachment_text([item], set(held), held, lookup)
        self.assertEqual(ok.missing_groups, [])
        self.assertIn("소프트웨어사업자", [n for g in ok.satisfied_groups for n in g.allowed_names])
        miss = qualify.evaluate_attachment_text([item], set(HELD), HELD, lookup)
        self.assertEqual([g.allowed_names for g in miss.missing_groups], [["소프트웨어사업자"]])

    def test_satisfied_or_bundle_is_kept(self):
        """'환경디자인(4442) 또는 종합디자인(4444)'을 둘 다 보유해도 충족 요건에 "또는" 묶음으로 남는다(2026-10-01)."""
        item = "다. 산업디자인전문회사로 환경디자인(업종코드: 4442) 또는 종합디자인(업종코드: 4444)으로 업종을 등록한 업체"
        held = {**HELD, "4444": "산업디자인전문회사(종합디자인분야)"}
        result = qualify.evaluate_attachment_text([item], set(held), held, LOOKUP)
        ors = [g.allowed_names for g in result.satisfied_groups if g.group_no == "또는"]
        self.assertEqual(ors, [["산업디자인전문회사(환경디자인분야)(4442)", "산업디자인전문회사(종합디자인분야)(4444)"]])

    def test_attachment_evaluation_uses_names(self):
        items = ["산업디자인 전문회사(시각디자인 분야)로 신고를 필한 업체", "실내건축공사업(업종코드 4990)을 등록한 업체"]
        result = qualify.evaluate_attachment_text(items, set(HELD), HELD, LOOKUP)
        self.assertEqual([g.allowed_names for g in result.missing_groups], [["산업디자인전문회사(시각디자인분야)"]])


class TestFlags(unittest.TestCase):
    def test_track_record_split_across_pdf_lines(self):
        """2026-10-01 영주 과수거점산지유통센터: PDF가 금액과 "실적"을 다른 줄로 갈라 칩이 빠졌었다."""
        item = ("나 . 입찰공고일 기준 최근 3 년 이내 정부 및 지방자치단체 , 공공기관 , 농 · 축협 ( 농협중앙회 \n"
                "     및 계열사 포함 ) 에서 발주한 실내건축공사로서 단일공사 5 억원 ( 부가세 포함 ) 이상 \n"
                "     준공실적을 보유한 업체 이어야  하며 , 하도급으로 \n     이행한 실적은 인정하지 않습니다 .")
        flags = flag_requirements([item])
        self.assertEqual([f["kind"] for f in flags], ["실적"])
        self.assertIn("5 억원", flags[0]["text"])
        self.assertIn("준공실적", flags[0]["text"])

    def test_submission_form_is_not_a_requirement(self):
        form = "서식 17-2 참여인력 경력사항\n성명 소속 자격증 분야 종류 기술자등급 취득일"
        self.assertEqual(flag_requirements([form]), [])

    def test_track_record(self):
        items = ["8) 입찰공고일 기준으로 최근 3년 이내에 … 전시장의 제작·설치 실적이 단일 건으로 10억원 이상 준공실적이 있는 업체"]
        self.assertEqual([f["kind"] for f in flag_requirements(items)], ["실적"])

    def test_track_record_not_restricted_or_scoring_is_ignored(self):
        """실적 제한이 없다는 안내·적격심사 평가(배점) 설명은 실적 요건이 아니다(2026-10-01 제보: 부산 50+복합지원센터)."""
        for s in ["1) 수행능력평가 중 시공경험평가는 시공실적으로 입찰참가자격을 제한하지 아니한 입찰이며, 평가대상 업종 및 "
                  "평가비율은 실내건축공사 100%, 849,560,000원 (추정가격)입니다.",
                  "최근 5년간 유사용역 수행실적 10억원 이상: 배점 5점"]:
            self.assertEqual(flag_requirements([s]), [], s)
        # "적격심사 세부기준에 따라" 단일실적을 요구하는 건 참가 요건 — 그대로 잡는다
        keep = "1) 최근 5년 이내에 '조달청 일반용역 적격심사 세부기준'에 따라 단일실적 334백만원 이상 실물모형을 제작·설치한 실적을 보유한 업체"
        self.assertEqual([f["kind"] for f in flag_requirements([keep])], ["실적"])

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


def test_attach_sources_names_file_and_zip_member():
    from nego.text_requirements import attach_sources

    flags = [
        {"kind": "인력", "text": "건축사 자격을 보유한 기술자 1인 이상 재직"},
        {"kind": "실적", "text": "최근 3년 이내 단일 실적 5억원 이상"},
        {"kind": "현장설명회", "text": "어디에도 없는 문장"},
    ]
    files = [
        ("제안요청서.hwp", "가. 건축사 자격을\n보유한 기술자 1인 이상\n재직 업체"),
        ("입찰서류.zip", "=== [압축 안] 공고문.pdf ===\n나. 최근 3년 이내 단일 실적\n5억원 이상"),
    ]
    out = attach_sources(flags, files)
    assert out[0]["source"] == "제안요청서.hwp"
    assert out[1]["source"] == "입찰서류.zip › 공고문.pdf"
    assert "source" not in out[2]
