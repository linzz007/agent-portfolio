from pathlib import Path
from types import SimpleNamespace
import sys
import unittest

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "code"))

from my_agents import (  # noqa: E402
    Calculator,
    CRITIC_PROMPT_TEMPLATE,
    CriticAgent,
    FinalAnswerAgent,
    LLMCallTracker,
    PLANNER_PROMPT_TEMPLATE,
    PlannerAgent,
    RouterAgent,
    TQASessionState,
    TableCompressor,
    TableQAPipeline,
    _build_table_schema,
    _canonicalize_crt_scalar,
    _canonicalize_wtq_scalar,
    _strip_entity_metadata,
    _strip_code_fence,
    build_df_from_table,
    validate_answer_contract_code_alignment,
    validate_generated_code_grounding,
)
from answer_contracts import infer_answer_contract, validate_contract_value  # noqa: E402
from dataset_profiles import infer_dataset_hints  # noqa: E402


class FakePipelineLLM:
    def __init__(
        self,
        semantic_score: float,
        rows,
        cols,
        classification_output='{"label":"true"}',
        direct_answer_output='{"answer":"100,000"}',
        planner_outputs=None,
        verification_output='{"label":"true"}',
        thinking_output='{"answer":"verified answer","confidence":0.9,"reasoning_summary":"checked independently"}',
    ):
        self.semantic_score = semantic_score
        self.rows = rows
        self.cols = cols
        self.classification_output = classification_output
        self.direct_answer_output = direct_answer_output
        self.planner_outputs = list(planner_outputs or [])
        self.verification_output = verification_output
        self.thinking_output = thinking_output
        self.prompts = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if "semantic parsing" in prompt:
            return f"{self.semantic_score:.2f}"
        if "estimating how many table cells" in prompt:
            import json

            return json.dumps({"rows": self.rows, "cols": self.cols})
        if "closed-label table classifier" in prompt:
            return self.classification_output
        if "TabFact verification judge" in prompt:
            return self.verification_output
        if "final high-risk verifier" in prompt:
            return self.thinking_output
        if "direct table QA extractor" in prompt:
            return self.direct_answer_output
        if "table reasoning planner and programmer" in prompt:
            if self.planner_outputs:
                return self.planner_outputs.pop(0)
            return (
                "[PLAN]\n"
                "Step1: Select 2020 and 2021 profit values.\n"
                "Step2: Subtract the 2020 value from the 2021 value.\n"
                "[CODE]\n"
                "values = df.set_index('Year')['Profit']\n"
                "final_answer_value = float(values.loc['2021']) - float(values.loc['2020'])\n"
            )
        if "careful auditor" in prompt:
            return "[VERDICT] PASS\n[COMMENT]\ncorrect\n[/COMMENT]\n[HINT_FOR_PLANNER]\nkeep\n[/HINT_FOR_PLANNER]"
        if "Evidence Critic" in prompt:
            return "[VERDICT] PASS\n[COMMENT]\nevidence retained\n[/COMMENT]"
        if "Logic Critic" in prompt:
            return "[VERDICT] PASS\n[COMMENT]\nlogic correct\n[/COMMENT]"
        if "[MAIN_PLAN]" in prompt and "[MAIN_VALUE]" in prompt:
            return (
                "[CODE]\n"
                "subset = df[df['Year'].isin(['2020', '2021'])].set_index('Year')\n"
                "final_answer_value = float(subset.loc['2021', 'Profit']) - float(subset.loc['2020', 'Profit'])\n"
            )
        return "20"


class CompleteAwareFakeLLM:
    def __init__(self):
        self.calls = []

    def complete(self, prompt: str, temperature: float = 0.0, max_tokens=None) -> str:
        self.calls.append(
            {"prompt": prompt, "temperature": temperature, "max_tokens": max_tokens}
        )
        return "done"


class MyAgentPipelineSmokeTests(unittest.TestCase):
    @staticmethod
    def _pipeline(fake_llm, enable_multi_view_validation=False):
        tracker = LLMCallTracker(fake_llm)
        pipeline = TableQAPipeline(
            router=RouterAgent(tracker),
            planner=PlannerAgent(tracker),
            calculator=Calculator(),
            critic=CriticAgent(tracker),
            final_answer_agent=FinalAnswerAgent(tracker),
            enable_multi_view_validation=enable_multi_view_validation,
            max_replan=2,
        )
        return pipeline, tracker

    def test_simple_path_routes_compresses_and_returns_one_cell(self):
        df = pd.DataFrame(
            {
                "Year": ["2020", "2021"],
                "Revenue": [100, 120],
                "Profit": [10, 30],
            }
        )
        fake = FakePipelineLLM(semantic_score=0.05, rows=["2021"], cols=["Revenue"])
        pipeline, tracker = self._pipeline(fake)
        state = TQASessionState(
            question="What was the Revenue in 2021?",
            df=df,
            table_schema=_build_table_schema(df),
        )

        result = pipeline.run(state)

        self.assertEqual(result.route_type, "SIMPLE")
        self.assertTrue(result.simple_lookup_success)
        self.assertEqual(result.simple_lookup_value, 120)
        self.assertEqual(result.final_value, 120)
        self.assertEqual(result.compression_info["strategy"], "strict_cell_block")
        self.assertLess(result.compression_info["compression_ratio"], 1.0)
        self.assertEqual(tracker.snapshot()["llm_call_count"], 2)

    def test_llm_tracker_complete_forwards_local_completion_budget(self):
        fake = CompleteAwareFakeLLM()
        tracker = LLMCallTracker(fake)

        output = tracker.complete("verify", temperature=0.0, max_tokens=512)

        self.assertEqual(output, "done")
        self.assertEqual(fake.calls[0]["max_tokens"], 512)
        self.assertEqual(tracker.snapshot()["llm_call_count"], 1)

    def test_scalar_normalization_extracts_single_value_series(self):
        df = pd.DataFrame({"Season": ["1989-1990 Season", "1990-1991 Season"]})
        fake = FakePipelineLLM(semantic_score=0.05, rows=["1990-1991 Season"], cols=["Season"])
        pipeline, _ = self._pipeline(fake)
        state = TQASessionState(
            question="what was the next tie listed after the 1989-1990 season?",
            df=df,
            table_schema=_build_table_schema(df),
            dataset_profile="wtq",
        )
        state.final_value = pd.Series(["1990-1991 Season"])

        self.assertTrue(pipeline._normalize_and_validate(state))
        self.assertEqual(state.final_value, "1990-1991 Season")
        self.assertEqual(state.final_answer, "1990-1991 Season")

    def test_scalar_normalization_rejects_multi_value_series_without_crashing(self):
        df = pd.DataFrame({"Season": ["1989-1990 Season", "1990-1991 Season"]})
        fake = FakePipelineLLM(semantic_score=0.05, rows=["1990-1991 Season"], cols=["Season"])
        pipeline, _ = self._pipeline(fake)
        state = TQASessionState(
            question="what was the next tie listed after the 1989-1990 season?",
            df=df,
            table_schema=_build_table_schema(df),
            dataset_profile="wtq",
        )
        state.final_value = pd.Series(["1990-1991 Season", "1990-1991 Season"])

        self.assertFalse(pipeline._normalize_and_validate(state))
        self.assertEqual(state.final_value, ["1990-1991 Season", "1990-1991 Season"])
        self.assertIn("one scalar", state.contract_validation["reason"])

    def test_state_infers_entity_list_answer_contract(self):
        df = pd.DataFrame({"Team": ["Alpha", "Beta"], "Races": [13, 14]})

        state = TQASessionState(
            question="Which teams raced at least 13 races?",
            df=df,
            table_schema=_build_table_schema(df),
        )

        self.assertEqual(state.answer_contract.kind, "list")
        self.assertTrue(state.answer_contract.reasoning_required)

    def test_wtq_scalar_entity_is_expanded_to_unique_exact_table_cell(self):
        df = pd.DataFrame({"Name": ["Ryan Dalziel", "Robert Doornbos"]})

        value = _canonicalize_wtq_scalar(
            "Dalziel",
            df,
            "Who was faster, Dalziel or Doornbos?",
        )

        self.assertEqual(value, "Ryan Dalziel")

    def test_wtq_numeric_scalar_is_not_expanded_to_entity_cell(self):
        df = pd.DataFrame({"Notes": ["B1-2 details"], "Medals": ["2"]})

        value = _canonicalize_wtq_scalar(
            "2",
            df,
            "how many silver medals did christian lanthaler receive?",
        )

        self.assertEqual(value, "2")

    def test_wtq_abbreviation_request_preserves_country_code_shape(self):
        df = pd.DataFrame({"Name": ["Li Na (CHN)", "Kim Clijsters (BEL)"]})

        self.assertEqual(
            _canonicalize_wtq_scalar(
                "China",
                df,
                "what country had the most amount of people in the top 10? (use abbreviation)",
            ),
            "CHN",
        )
        self.assertEqual(
            _canonicalize_wtq_scalar(
                "CHN",
                df,
                "what country had the most amount of people in the top 10? use abbreviation",
            ),
            "CHN",
        )

    def test_wtq_difference_between_canonicalizes_negative_numeric_delta(self):
        df = pd.DataFrame({"Player": ["first", "fourth"], "Balls": [10, 4]})

        value = _canonicalize_wtq_scalar(
            -6,
            df,
            "what is the difference in balls between the first and fourth players?",
        )

        self.assertEqual(value, 6)

    def test_wtq_difference_of_canonicalizes_negative_numeric_delta(self):
        df = pd.DataFrame({"Team": ["JSU"], "Score JSU": [6], "Score TU": [24]})

        value = _canonicalize_wtq_scalar(
            -18,
            df,
            "what is the difference of the jsu and tu scores in 2001",
        )

        self.assertEqual(value, 18)

    def test_crt_combination_scalar_is_reordered_by_table_column_order(self):
        df = pd.DataFrame(
            {
                "directed by": ["dean parisot"],
                "written by": ["ted humphrey"],
                "us viewers": [12.76],
            }
        )

        value = _canonicalize_crt_scalar(
            "ted humphrey & dean parisot",
            "Which combination of writer and director had the highest average viewers?",
            df,
        )

        self.assertEqual(value, "dean parisot, ted humphrey")

    def test_crt_percentage_scalar_canonicalizes_integer_decimal_percent(self):
        value = _canonicalize_crt_scalar(
            "80.0%",
            "what percentage of medals were won by nations from Europe?",
            pd.DataFrame({"total": [12]}),
        )

        self.assertEqual(value, "80%")

    def test_crt_proportion_scalar_is_formatted_as_fraction(self):
        df = pd.DataFrame({"company": list("abcdef")})

        value = _canonicalize_crt_scalar(
            1 / 6,
            "What proportion of the Malaysia Airlines group companies are involved in the airline industry?",
            df,
        )

        self.assertEqual(value, "1/6")

    def test_crt_average_contract_preserves_three_decimal_precision(self):
        hints = infer_dataset_hints(
            "crt",
            "What was the average viewership for episodes that aired in March 2006?",
            pd.DataFrame({"viewers": [13.5, 13.77]}),
        )

        self.assertEqual(hints.decimal_places, 3)

    def test_scalar_contract_rejects_nan_value(self):
        contract = infer_answer_contract("What is the average number of viewers?")

        valid, reason = validate_contract_value(float("nan"), contract)

        self.assertFalse(valid)
        self.assertIn("non-NaN", reason)

    def test_crt_numeric_execution_candidate_is_preserved_over_conflicting_verifier(self):
        state = TQASessionState(
            question="What is the average number of losses for teams in the top half of the table?",
            df=pd.DataFrame({"losses": [7, 9, 9, 8, 9, 7, 11, 11]}),
            table_schema={},
            dataset_profile="crt",
        )
        selected = SimpleNamespace(
            name="code",
            is_valid=True,
            normalized_answer=8.875,
        )
        consensus = SimpleNamespace(
            name="thinking_program",
            is_valid=True,
            normalized_answer=9.0,
        )

        self.assertTrue(
            TableQAPipeline._should_preserve_crt_numeric_execution(
                state,
                selected,
                consensus,
                forced=False,
            )
        )

    def test_entity_metadata_suffix_is_removed_from_requested_name(self):
        self.assertEqual(
            _strip_entity_metadata("monster release date : june 28, 2006"),
            "monster",
        )
        self.assertEqual(
            _strip_entity_metadata("The Greatest Story Never Told"),
            "The Greatest Story Never Told",
        )

    def test_planner_prompt_contains_answer_contract(self):
        df = pd.DataFrame({"Team": ["Alpha", "Beta"], "Races": [13, 14]})
        fake = FakePipelineLLM(semantic_score=0.8, rows=[], cols=["Team", "Races"])
        state = TQASessionState(
            question="Which teams raced at least 13 races?",
            df=df,
            table_schema=_build_table_schema(df),
        )

        PlannerAgent(fake).plan(state)

        planner_prompt = fake.prompts[-1]
        self.assertIn("Return the entities, not their count", planner_prompt)

    def test_state_uses_supplied_contract_and_dataset_instructions(self):
        df = pd.DataFrame({"Venue": ["home", "away"], "Wins": [8, 3]})
        contract = infer_answer_contract(
            "Was the team more successful at home or away?",
            allowed_labels=("home", "away"),
            kind_override="label",
            reasoning_required=True,
        )
        state = TQASessionState(
            question="Was the team more successful at home or away?",
            df=df,
            table_schema=_build_table_schema(df),
            answer_contract=contract,
            dataset_profile="crt",
            dataset_instructions="Compare like-for-like units.",
        )
        fake = FakePipelineLLM(semantic_score=0.8, rows=[], cols=["Venue", "Wins"])

        PlannerAgent(fake).plan(state)

        self.assertIs(state.answer_contract, contract)
        self.assertEqual(state.dataset_profile, "crt")
        self.assertIn("Compare like-for-like units.", fake.prompts[-1])

    def test_tuple_final_answer_is_machine_readable_json(self):
        df = pd.DataFrame({"Result": ["decision", "finish"]})
        contract = infer_answer_contract(
            "How many by decision and how many by finish?",
            kind_override="tuple",
            arity=2,
            reasoning_required=True,
        )
        state = TQASessionState(
            question="How many by decision and how many by finish?",
            df=df,
            table_schema=_build_table_schema(df),
            answer_contract=contract,
        )
        state.route_type = "COMPLEX"
        state.final_value = [3, 12]

        result = FinalAnswerAgent(lambda _: "unused").respond(state)

        self.assertEqual(result.final_answer, "[3, 12]")

    def test_invalid_complex_answer_uses_structured_recovery(self):
        df = pd.DataFrame({"Title": ["Miss Malaga 2011"], "Winner": ["Ursula"]})
        fake = FakePipelineLLM(
            semantic_score=0.8,
            rows=[],
            cols=["Winner"],
            direct_answer_output='{"answer":"Ursula"}',
        )
        state = TQASessionState(
            question="Who held the Miss Malaga 2011 title?",
            df=df,
            table_schema=_build_table_schema(df),
        )
        state.route_type = "COMPLEX"
        state.final_value = ""
        state.contract_validation = {
            "valid": False,
            "reason": "Answer contract requires one non-empty scalar value.",
        }

        result = FinalAnswerAgent(fake).respond(state)

        self.assertEqual(result.final_value, "Ursula")
        self.assertEqual(result.final_answer, "Ursula")
        self.assertIn("structured recovery", fake.prompts[-1])

    def test_planner_prompt_contains_full_table_column_profiles(self):
        df = pd.DataFrame(
            {
                "Fate": ["Fell"] * 8 + ["Pulled Up", "Refused"],
                "Runner": [f"Horse {index}" for index in range(10)],
            }
        )
        fake = FakePipelineLLM(semantic_score=0.8, rows=[], cols=["Fate"])
        state = TQASessionState(
            question="How many runners did not finish?",
            df=df,
            table_schema=_build_table_schema(df, max_preview_rows=8),
        )

        PlannerAgent(fake).plan(state)

        planner_prompt = fake.prompts[-1]
        self.assertIn("[COLUMN PROFILES]", planner_prompt)
        self.assertIn("Pulled Up", planner_prompt)

    def test_planner_prompt_contains_table_grounding_rules(self):
        df = pd.DataFrame(
            {
                "Team": ["Alpha", "Beta"],
                "Coach": ["unknown", "known"],
                "Location": ["Adelaide", "Perth"],
            }
        )
        fake = FakePipelineLLM(semantic_score=0.8, rows=[], cols=["Coach"])
        state = TQASessionState(
            question="One of the teams in Australia has an unknown coach.",
            df=df,
            table_schema=_build_table_schema(df),
            answer_mode="true_false",
            table_context="List of soccer clubs in Australia",
        )

        PlannerAgent(fake).plan(state)

        planner_prompt = fake.prompts[-1]
        self.assertIn("DataFrame shape: 2 rows x 3 columns", planner_prompt)
        self.assertIn("table-level context", planner_prompt)
        self.assertIn("distinct or unique", planner_prompt)
        self.assertIn("List of soccer clubs in Australia", planner_prompt)
        self.assertIn("tend", planner_prompt)
        self.assertIn("conserved", planner_prompt)
        self.assertIn("former titleholder", planner_prompt)
        self.assertIn("Do not drop summary, sum, total, or aggregate rows", planner_prompt)
        self.assertIn('asking "after 1936" excludes', planner_prompt)
        self.assertIn("percentage columns are snapshots", planner_prompt)
        self.assertIn("systematic pattern", planner_prompt)

    def test_wtq_country_code_scalar_expands_to_country_name(self):
        df = pd.DataFrame(
            {
                "Cyclist": [
                    "Mario Cipollini (ITA)",
                    "Paolo Bettini (ITA)",
                    "Tom Boonen (BEL)",
                ]
            }
        )

        self.assertEqual(
            _canonicalize_wtq_scalar(
                "ITA",
                df,
                "which country had the most cyclists finish within the top 10?",
            ),
            "Italy",
        )

    def test_wtq_country_code_pipeline_syncs_final_answer(self):
        df = pd.DataFrame(
            {
                "Rank": [1, 2, 3],
                "Cyclist": [
                    "Mario Cipollini (ITA)",
                    "Paolo Bettini (ITA)",
                    "Tom Boonen (BEL)",
                ],
            }
        )
        planner_output = (
            "[PLAN]\n"
            "Step1: Count country codes among top ranked cyclists.\n"
            "[CODE]\n"
            "final_answer_value = 'ITA'\n"
        )
        fake = FakePipelineLLM(
            semantic_score=0.8,
            rows=["1", "2", "3"],
            cols=["Rank", "Cyclist"],
            planner_outputs=[planner_output],
        )
        pipeline, _ = self._pipeline(fake)
        state = TQASessionState(
            question="which country had the most cyclists finish within the top 10?",
            df=df,
            table_schema=_build_table_schema(df),
            dataset_profile="wtq",
        )

        result = pipeline.run(state)

        self.assertEqual(result.final_value, "Italy")
        self.assertEqual(result.final_answer, "Italy")

    def test_wtq_how_long_after_year_adds_year_unit(self):
        df = pd.DataFrame({"Year": ["1936/37", "1953/54"]})

        self.assertEqual(
            _canonicalize_wtq_scalar(
                17,
                df,
                "how long did it take to win after 1936?",
            ),
            "17 years",
        )

    def test_wtq_datetime_scalar_formats_as_date_text(self):
        df = pd.DataFrame({"Original air date": ["January 26, 1995"]})

        self.assertEqual(
            _canonicalize_wtq_scalar(
                pd.Timestamp("1995-01-26").to_datetime64(),
                df,
                "which episode aired on january 19 and what was the next airdate?",
            ),
            "January 26, 1995",
        )

    def test_build_df_removes_repeated_header_rows(self):
        table = [
            ["week", "record"],
            ["week", "record"],
            ["1", "0 - 1"],
            ["2", "1 - 1"],
        ]

        df = build_df_from_table(table)

        self.assertEqual(df["record"].tolist(), ["0 - 1", "1 - 1"])
        self.assertEqual(df["week"].tolist(), [1.0, 2.0])

    def test_build_df_handles_quotes_in_column_names_without_code_generation(self):
        table = [
            ["company", "group 's equity shareholding"],
            ["Example Air", "30%"],
        ]

        df = build_df_from_table(table)

        self.assertEqual(
            list(df.columns),
            ["company", "group 's equity shareholding"],
        )
        self.assertEqual(df.iloc[0].tolist(), ["Example Air", "30%"])

    def test_build_df_normalizes_blank_and_annotated_numeric_cells(self):
        table = [
            ["Name", "Points", "Sales"],
            ["A", "318.65", "1,336,150 +"],
            ["B", "", "401000 +"],
        ]

        df = build_df_from_table(table)

        self.assertEqual(df["Points"].min(), 318.65)
        self.assertTrue(pd.isna(df.loc[1, "Points"]))
        self.assertEqual(df["Sales"].tolist(), [1336150.0, 401000.0])

    def test_schema_profiles_include_values_beyond_preview_and_missing_markers(self):
        df = pd.DataFrame(
            {
                "Age": list(range(1, 13)),
                "Fate": ["Fell"] * 8
                + ["Pulled Up", "Brought Down", "Refused", "TBA"],
                "Mixed": [1, "two"] + [None] * 10,
            }
        )

        schema = _build_table_schema(df, max_preview_rows=8)

        fate = schema["column_profiles"]["Fate"]
        self.assertEqual(fate["semantic_type"], "text")
        self.assertEqual(
            fate["representative_values"],
            ["Fell", "Pulled Up", "Brought Down", "Refused", "TBA"],
        )
        self.assertEqual(fate["missing_marker_count"], 1)
        self.assertEqual(schema["column_profiles"]["Age"]["semantic_type"], "numeric")
        self.assertEqual(schema["column_profiles"]["Mixed"]["semantic_type"], "mixed")
        self.assertIn("Pulled Up", schema["column_profiles_text"])
        self.assertNotIn("Pulled Up", schema["preview_text"])

    def test_schema_profile_text_is_bounded_for_wide_tables(self):
        df = pd.DataFrame(
            {
                f"Column {column}": [f"value-{column}-{row}" for row in range(30)]
                for column in range(80)
            }
        )

        schema = _build_table_schema(df)

        self.assertLessEqual(len(schema["column_profiles_text"]), 8000)

    def test_generated_code_strips_standalone_xml_end_tags(self):
        raw = (
            "```python\n"
            "value = 1 < 2\n"
            "final_answer_value = 'true'\n"
            "</final_answer_value>\n"
            "```"
        )

        cleaned = _strip_code_fence(raw)

        self.assertIn("value = 1 < 2", cleaned)
        self.assertNotIn("</final_answer_value>", cleaned)

    def test_grounding_validator_rejects_unique_count_for_x_of_n(self):
        valid, reason = validate_generated_code_grounding(
            "Grass was the surface in 3 of the 14 championships.",
            "total = df['championship'].nunique()\nfinal_answer_value = total == 14",
        )

        self.assertFalse(valid)
        self.assertIn("Use row counts", reason)

    def test_grounding_validator_allows_explicit_distinct_count(self):
        valid, reason = validate_generated_code_grounding(
            "Were there 3 distinct championships?",
            "final_answer_value = df['championship'].nunique() == 3",
        )

        self.assertTrue(valid)
        self.assertEqual(reason, "")

    def test_grounding_validator_requires_reference_exclusion_for_same_group(self):
        invalid, reason = validate_generated_code_grounding(
            "How many elements are in the same group as Neon?",
            "group = df.loc[df['Name'] == 'Neon', 'Group'].iloc[0]\n"
            "final_answer_value = (df['Group'] == group).sum()",
        )
        invalid_with_unrelated_subtraction, _ = validate_generated_code_grounding(
            "How many elements are in the same group as Neon?",
            "offset = 5 - 1\n"
            "group = df.loc[df['Name'] == 'Neon', 'Group'].iloc[0]\n"
            "final_answer_value = (df['Group'] == group).sum()",
        )
        valid, _ = validate_generated_code_grounding(
            "How many elements are in the same group as Neon?",
            "group = df.loc[df['Name'] == 'Neon', 'Group'].iloc[0]\n"
            "final_answer_value = ((df['Group'] == group) & "
            "(df['Name'] != 'Neon')).sum()",
        )

        self.assertFalse(invalid)
        self.assertFalse(invalid_with_unrelated_subtraction)
        self.assertIn("Exclude the named reference item", reason)
        self.assertTrue(valid)

    def test_grounding_validator_rejects_unrequested_summary_exclusion(self):
        code = (
            "# Exclude summary rows if any\n"
            "df_counties = df[~df['county'].str.lower().eq('norway')].copy()\n"
            "final_answer_value = df_counties['county'].iloc[0]"
        )

        valid, reason = validate_generated_code_grounding(
            "Which county has had the most consistent percentage change?",
            code,
        )

        self.assertFalse(valid)
        self.assertIn("Do not exclude summary", reason)

    def test_grounding_validator_rejects_after_year_inclusive_boundary(self):
        code = "rows = df[df['start_year'] >= 1936]\nfinal_answer_value = rows.iloc[0]['Year']"

        valid, reason = validate_generated_code_grounding(
            "How long did it take to win after 1936?",
            code,
        )

        self.assertFalse(valid)
        self.assertIn("after 1936", reason)

    def test_grounding_validator_rejects_relative_formula_for_snapshot_average(self):
        code = (
            "top5 = df[df['rank'] <= 5]\n"
            "pct_change = ((top5['% (2040)'] - top5['% (1960)']) / top5['% (1960)']) * 100\n"
            "final_answer_value = round(pct_change.mean(), 3)"
        )

        valid, reason = validate_generated_code_grounding(
            "What is the average percentage change in population between 1960 and 2040?",
            code,
        )

        self.assertFalse(valid)
        self.assertIn("percentage snapshot", reason)

    def test_grounding_validator_rejects_hardcoded_label_after_condition(self):
        code = (
            "if df['days'].nunique() > 1:\n"
            "    final_answer_value = 'Yes'\n"
            "else:\n"
            "    final_answer_value = 'No'\n"
            "final_answer_value = 'Yes'\n"
        )

        valid, reason = validate_generated_code_grounding(
            "Is there a difference in the types of events based on stages?",
            code,
        )

        self.assertFalse(valid)
        self.assertIn("hard-coded", reason)

    def test_contract_code_alignment_rejects_wrong_rounding_precision(self):
        contract = infer_answer_contract(
            "What is the average percentage change?",
            decimal_places=3,
        )
        code = "final_answer_value = round(avg_change, 0)"

        valid, reason = validate_answer_contract_code_alignment(code, contract)

        self.assertFalse(valid)
        self.assertIn("3 decimal", reason)

    def test_grounding_validator_rejects_combining_all_peer_groups(self):
        valid, reason = validate_generated_code_grounding(
            "How does Mandsaur compare to the other districts?",
            "target = df[df['district'] == 'mandsaur']['count'].sum()\n"
            "others = df[df['district'] != 'mandsaur']['count'].sum()\n"
            "final_answer_value = 'more' if target > others else 'less'",
        )

        self.assertFalse(valid)
        self.assertIn("each peer group", reason)

    def test_reasoning_prompts_are_domain_neutral(self):
        self.assertNotIn("Chinese question", PLANNER_PROMPT_TEMPLATE)
        self.assertNotIn("governmental statistics", PLANNER_PROMPT_TEMPLATE)
        self.assertNotIn("governmental statistics", CRITIC_PROMPT_TEMPLATE)

    def test_simple_fallback_records_a_structured_answer_value(self):
        df = pd.DataFrame(
            {
                "Period": ["1939/40", "1940/41"],
                "Killed": ["80,000", "100,000"],
                "Missing": ["5,000", "6,000"],
            }
        )
        fake = FakePipelineLLM(
            semantic_score=0.05,
            rows=["1940/41"],
            cols=["Killed", "Missing"],
            direct_answer_output='{"answer":"100,000"}',
        )
        pipeline, tracker = self._pipeline(fake)
        state = TQASessionState(
            question="What was the affected figure in 1940/41?",
            df=df,
            table_schema=_build_table_schema(df),
        )

        result = pipeline.run(state)

        self.assertFalse(result.simple_lookup_success)
        self.assertEqual(result.final_value, "100,000")
        self.assertEqual(result.final_answer, "100,000")
        self.assertEqual(tracker.snapshot()["llm_call_count"], 3)

    def test_simple_path_normalizes_format_only_noise(self):
        df = pd.DataFrame(
            {
                "Country": ["Canada/United States", "Australia"],
                "Box Office": ["$10.8 billion", "$1.2 billion"],
            }
        )
        fake = FakePipelineLLM(
            semantic_score=0.05,
            rows=["Canada/United States", "Australia"],
            cols=["Box Office"],
            direct_answer_output='{"answer":"$12.0 billion"}',
        )
        pipeline, _ = self._pipeline(fake)
        state = TQASessionState(
            question="How much box office revenue did they account for?",
            df=df,
            table_schema=_build_table_schema(df),
        )

        result = pipeline.run(state)

        self.assertEqual(result.final_value, "$12 billion")
        self.assertEqual(result.contract_validation, {"valid": True, "reason": ""})

    def test_calculator_rejects_ellipsis_placeholder_answer(self):
        state = TQASessionState(
            question="What is the answer?",
            df=pd.DataFrame({"A": [1]}),
            table_schema={},
        )
        state.code_str = "final_answer_value = ..."

        result = Calculator().execute(state)

        self.assertFalse(result.exec_success)
        self.assertIsNone(result.final_value)
        self.assertIn("ellipsis", result.exec_error.lower())

    def test_aggregate_compression_keeps_every_row_and_exposes_last_row(self):
        df = pd.DataFrame(
            {
                "Round": list(range(20)),
                "Home/Away": ["Away" if i % 2 else "Home" for i in range(20)],
            }
        )
        state = TQASessionState(
            question="How many total away games were played?",
            df=df,
            table_schema=_build_table_schema(df),
        )
        state.original_df = df
        state.difficulty_level = "easy"
        state.structural_features = {
            "selected_rows": [],
            "selected_cols": ["Home/Away"],
            "cell_score": 0.1,
        }

        result = TableCompressor(max_easy_rows=12).compress(state)

        self.assertEqual(result.compression_info["compressed_rows"], 20)
        self.assertIn("19", result.table_schema["preview_text"])

    def test_geographic_aggregation_keeps_column_containing_question_values(self):
        df = pd.DataFrame(
            {
                "Circuit": ["Sandown", "Bathurst", "Homebush"],
                "Location": [
                    "Melbourne, Victoria",
                    "Bathurst, New South Wales",
                    "Sydney, New South Wales",
                ],
            }
        )
        state = TQASessionState(
            question="How many races were held in Victoria or New South Wales?",
            df=df,
            table_schema=_build_table_schema(df),
        )
        state.original_df = df
        state.route_type = "COMPLEX"
        state.difficulty_level = "medium"
        state.structural_features = {
            "selected_rows": [],
            "selected_cols": ["Circuit"],
            "cell_score": 0.3,
        }

        result = TableCompressor().compress(state)

        self.assertIn("Location", result.compression_info["used_cols"])

    def test_difference_compression_keeps_adjacent_repeated_metric_column(self):
        df = pd.DataFrame(
            {
                "County": ["Plumas", "Trinity"],
                "Brown": ["66.44%", "64.58%"],
                "Votes": ["3,397", "2,201"],
                "Nixon": ["31.76%", "33.69%"],
                "Votes_2": ["1,624", "1,148"],
                "Wyckoff": ["1.80%", "1.73%"],
                "Votes_3": ["92", "59"],
            }
        )
        state = TQASessionState(
            question="In Plumas, what was the difference between Brown and Nixon's votes?",
            df=df,
            table_schema=_build_table_schema(df),
        )
        state.original_df = df
        state.route_type = "COMPLEX"
        state.difficulty_level = "medium"
        state.structural_features = {
            "selected_rows": ["Plumas"],
            "selected_cols": ["County", "Brown", "Votes", "Nixon"],
            "cell_score": 0.05,
        }

        result = TableCompressor().compress(state)

        self.assertIn("Votes_2", result.compression_info["used_cols"])

    def test_how_often_before_date_keeps_all_rows(self):
        df = pd.DataFrame(
            {"Air date": [f"November {day}, 2007" for day in range(1, 21)]}
        )
        state = TQASessionState(
            question="Previous to November 15, 2007 how often was the rank in the 30s?",
            df=df,
            table_schema=_build_table_schema(df),
        )
        state.original_df = df
        state.route_type = "COMPLEX"
        state.difficulty_level = "medium"
        state.structural_features = {
            "selected_rows": ["November 15, 2007"],
            "selected_cols": ["Air date"],
            "cell_score": 0.1,
        }

        result = TableCompressor().compress(state)

        self.assertEqual(result.compression_info["compressed_rows"], 20)

    def test_wtq_extreme_and_only_questions_keep_all_rows(self):
        df = pd.DataFrame(
            {
                "Driver": [f"Driver {idx}" for idx in range(20)],
                "Car": ["Ford"] * 14 + ["Saab"] + ["BMW"] * 5,
                "Date": [f"2020-01-{idx + 1:02d}" for idx in range(20)],
                "Speed": [100 + idx for idx in range(20)],
            }
        )
        questions = [
            "Which driver drove the only Saab car?",
            "What was the latest date listed?",
            "Which driver finished first?",
            "Who had the top speed?",
        ]

        for question in questions:
            with self.subTest(question=question):
                state = TQASessionState(
                    question=question,
                    df=df,
                    table_schema=_build_table_schema(df),
                )
                state.original_df = df
                state.route_type = "COMPLEX"
                state.difficulty_level = "easy"
                state.structural_features = {
                    "selected_rows": [],
                    "selected_cols": ["Driver", "Car", "Date", "Speed"],
                    "cell_score": 0.1,
                }

                result = TableCompressor(max_easy_rows=12).compress(state)

                self.assertEqual(result.compression_info["compressed_rows"], 20)
                self.assertIn("global_rows", result.compression_info["strategy"])

    def test_wtq_earlier_later_option_comparison_keeps_all_rows(self):
        df = pd.DataFrame(
            {
                "Place": (
                    ["Coral Springs, Florida"]
                    + [f"Other Place {idx}" for idx in range(18)]
                    + ["Sydney, Australia"]
                ),
                "Date": (
                    ["2008"]
                    + [f"2009-01-{idx + 1:02d}" for idx in range(18)]
                    + ["2010"]
                ),
            }
        )
        state = TQASessionState(
            question="which was earlier, syndney, australia or coral springs, florida?",
            df=df,
            table_schema=_build_table_schema(df),
        )
        state.original_df = df
        state.route_type = "COMPLEX"
        state.difficulty_level = "easy"
        state.structural_features = {
            "selected_rows": ["Coral Springs, Florida"],
            "selected_cols": ["Place", "Date"],
            "cell_score": 0.1,
        }

        result = TableCompressor(max_easy_rows=12).compress(state)

        self.assertEqual(result.compression_info["compressed_rows"], 20)
        self.assertIn("global_rows", result.compression_info["strategy"])
        self.assertIn("Sydney, Australia", result.df["Place"].tolist())

    def test_relative_row_compression_keeps_neighbor_and_label_column(self):
        df = pd.DataFrame(
            {
                "Rank": [1, 2, 3],
                "Country": ["Alpha", "Canada", "Australia"],
                "Revenue": [10, 20, 30],
                "Notes": ["a", "b", "c"],
            }
        )
        state = TQASessionState(
            question="What country is next after Canada?",
            df=df,
            table_schema=_build_table_schema(df),
        )
        state.original_df = df
        state.difficulty_level = "easy"
        state.structural_features = {
            "selected_rows": ["Canada"],
            "selected_cols": ["Revenue"],
            "cell_score": 0.1,
        }

        result = TableCompressor(max_easy_rows=12).compress(state)

        self.assertEqual(result.compression_info["used_rows"], ["0", "1", "2"])
        self.assertIn("Country", result.compression_info["used_cols"])
        self.assertIn("Notes", result.compression_info["used_cols"])

    def test_row_compression_matches_question_tokens_beyond_first_three_cells(self):
        """Catches WTQ evidence loss when entity clues live in a late description column."""
        df = pd.DataFrame(
            {
                "Year": [1971, 1972, 1974],
                "Film": ["The Raging Moon", "Eagle in a Cage", "Mahler"],
                "Role": ["Jill", "Betsy Balcombe", "Alma Mahler"],
                "Notes": [
                    "Drama nomination",
                    "Historical film",
                    "Georgina Hale received her BAFTA award for this performance",
                ],
            }
        )
        state = TQASessionState(
            question="For which film did Georgina Hale receive her BAFTA award?",
            df=df,
            table_schema=_build_table_schema(df),
        )
        state.original_df = df
        state.route_type = "COMPLEX"
        state.difficulty_level = "easy"
        state.structural_features = {
            "selected_rows": [],
            "selected_cols": ["Film"],
            "cell_score": 0.1,
        }

        result = TableCompressor(max_easy_rows=2).compress(state)

        self.assertIn("2", result.compression_info["used_rows"])
        self.assertIn("Mahler", result.df["Film"].tolist())

    def test_complex_compression_keeps_full_data_but_short_planner_preview(self):
        df = pd.DataFrame(
            {
                "Round": list(range(20)),
                "Home/Away": ["Away" if i % 2 else "Home" for i in range(20)],
            }
        )
        state = TQASessionState(
            question="How many away games were played?",
            df=df,
            table_schema=_build_table_schema(df),
        )
        state.original_df = df
        state.route_type = "COMPLEX"
        state.difficulty_level = "medium"
        state.structural_features = {
            "selected_rows": [],
            "selected_cols": ["Home/Away"],
            "cell_score": 0.1,
        }

        result = TableCompressor().compress(state)

        self.assertEqual(result.compression_info["compressed_rows"], 20)
        self.assertNotIn("19", result.table_schema["preview_text"])

    def test_cross_column_superlative_keeps_all_year_columns(self):
        df = pd.DataFrame(
            {
                "Tournament": ["win - loss"],
                "2006": ["1 - 3"],
                "2007": ["8 - 4"],
                "2008": ["4 - 4"],
            }
        )
        state = TQASessionState(
            question="2007 was the year with the best win-loss ratio.",
            df=df,
            table_schema=_build_table_schema(df),
            answer_mode="true_false",
        )
        state.original_df = df
        state.route_type = "COMPLEX"
        state.difficulty_level = "medium"
        state.structural_features = {
            "selected_rows": ["win - loss"],
            "selected_cols": ["Tournament", "2007"],
            "cell_score": 0.25,
        }

        result = TableCompressor().compress(state)

        self.assertEqual(result.compression_info["used_cols"], list(df.columns))
        self.assertIn("global_columns", result.compression_info["strategy"])

    def test_abstract_performance_comparison_keeps_metric_columns(self):
        df = pd.DataFrame(
            {
                "Year": [2002, 2003],
                "Competition": ["A", "B"],
                "Position": ["100 m", "100 m"],
                "Event": ["1st", "2nd"],
                "Notes": ["11.3 secs", "11.1 secs"],
            }
        )
        state = TQASessionState(
            question="How does her early performance compare to later competitions?",
            df=df,
            table_schema=_build_table_schema(df),
        )
        state.original_df = df
        state.route_type = "COMPLEX"
        state.difficulty_level = "medium"
        state.structural_features = {
            "selected_rows": [],
            "selected_cols": ["Competition", "Position"],
            "cell_score": 0.2,
        }

        result = TableCompressor().compress(state)

        self.assertEqual(result.compression_info["used_cols"], list(df.columns))

    def test_aggregate_question_forces_complex_route(self):
        df = pd.DataFrame(
            {
                "Round": list(range(20)),
                "Home/Away": ["Away" if i % 2 else "Home" for i in range(20)],
            }
        )
        fake = FakePipelineLLM(
            semantic_score=0.05,
            rows=[],
            cols=["Home/Away"],
        )
        state = TQASessionState(
            question="How many total away games were played?",
            df=df,
            table_schema=_build_table_schema(df),
        )

        result = RouterAgent(fake).route(state)

        self.assertEqual(result.route_type, "COMPLEX")

    def test_single_entity_how_many_value_stays_simple(self):
        df = pd.DataFrame(
            {
                "Team": ["KR", "Alpha"],
                "Goals": [27, 12],
            }
        )
        fake = FakePipelineLLM(
            semantic_score=0.05,
            rows=["KR"],
            cols=["Goals"],
        )
        state = TQASessionState(
            question="How many goals did KR score?",
            df=df,
            table_schema=_build_table_schema(df),
        )

        result = RouterAgent(fake).route(state)

        self.assertEqual(result.route_type, "SIMPLE")

    def test_relative_row_question_skips_single_cell_shortcut(self):
        df = pd.DataFrame(
            {
                "Name": ["Alpha", "Canada", "Australia"],
            }
        )
        fake = FakePipelineLLM(
            semantic_score=0.05,
            rows=["Canada"],
            cols=["Name"],
            direct_answer_output='{"answer":"Australia"}',
        )
        pipeline, _ = self._pipeline(fake)
        state = TQASessionState(
            question="What is next after Canada?",
            df=df,
            table_schema=_build_table_schema(df),
        )

        result = pipeline.run(state)

        self.assertFalse(result.simple_lookup_success)
        self.assertEqual(result.final_value, "Australia")
        self.assertEqual(
            result.simple_lookup_evidence["reason"],
            "relative_row_question_requires_context",
        )

    def test_semantic_score_ignores_unrelated_step_counts(self):
        router = RouterAgent(lambda _: "This needs 2 operations. Final score: 0.25")

        score = router.llm_semantic_score("Compare two values.")

        self.assertEqual(score, 0.25)

    def test_calculator_allows_only_approved_numeric_imports(self):
        df = pd.DataFrame({"Value": ["10", "20"]})
        calculator = Calculator()
        allowed_state = TQASessionState(
            question="What is the total?",
            df=df,
            table_schema=_build_table_schema(df),
        )
        allowed_state.code_str = (
            "import pandas as pd\n"
            "print(df.head())\n"
            "final_answer_value = int(pd.to_numeric(df['Value']).sum())"
        )

        allowed_result = calculator.execute(allowed_state)

        self.assertTrue(allowed_result.exec_success)
        self.assertEqual(allowed_result.final_value, 30)

        blocked_state = TQASessionState(
            question="Read a system file.",
            df=df,
            table_schema=_build_table_schema(df),
        )
        blocked_state.code_str = "import os\nfinal_answer_value = os.getcwd()"

        blocked_result = calculator.execute(blocked_state)

        self.assertFalse(blocked_result.exec_success)
        self.assertIn("not allowed", blocked_result.exec_error)

    def test_calculator_allows_regex_and_isinstance_without_opening_os(self):
        df = pd.DataFrame({"Result": ["Age 21", "Age 34"]})
        state = TQASessionState(
            question="How many ages are present?",
            df=df,
            table_schema=_build_table_schema(df),
        )
        state.code_str = (
            "import re\n"
            "matches = df['Result'].apply(lambda value: re.search(r'\\d+', value))\n"
            "final_answer_value = sum(isinstance(match.group(0), str) for match in matches)\n"
        )

        result = Calculator().execute(state)

        self.assertTrue(result.exec_success)
        self.assertEqual(result.final_value, 2)

    def test_calculator_helper_function_is_visible_inside_apply(self):
        df = pd.DataFrame({"Score": ["1-2", "3-1"]})
        state = TQASessionState(
            question="What are the score differences?",
            df=df,
            table_schema=_build_table_schema(df),
        )
        state.code_str = (
            "def difference(score):\n"
            "    left, right = score.split('-')\n"
            "    return abs(int(left) - int(right))\n"
            "values = df['Score'].apply(lambda value: difference(value))\n"
            "final_answer_value = int(values.sum())\n"
        )

        result = Calculator().execute(state)

        self.assertTrue(result.exec_success)
        self.assertEqual(result.final_value, 3)

    def test_complex_path_executes_and_passes_multi_view_validation(self):
        df = pd.DataFrame(
            {
                "Year": ["2019", "2020", "2021"],
                "Revenue": [90, 100, 120],
                "Profit": [5, 10, 30],
            }
        )
        fake = FakePipelineLLM(semantic_score=0.90, rows=["2020", "2021"], cols=["Profit"])
        pipeline, tracker = self._pipeline(fake, enable_multi_view_validation=True)
        state = TQASessionState(
            question="How much did Profit increase from 2020 to 2021?",
            df=df,
            table_schema=_build_table_schema(df),
            answer_contract=infer_answer_contract(
                "How much did Profit increase from 2020 to 2021?",
                reasoning_required=True,
            ),
        )

        result = pipeline.run(state)

        self.assertEqual(result.route_type, "COMPLEX")
        self.assertTrue(result.exec_success)
        self.assertEqual(result.final_value, 20.0)
        self.assertEqual(result.critic_verdict, "PASS")
        self.assertEqual(result.multi_view_validation["verdict"], "PASS")
        self.assertTrue(result.alternative_exec_success)
        self.assertEqual(result.alternative_final_value, 20.0)
        self.assertEqual(result.cross_validation_verdict, "PASS")
        self.assertGreaterEqual(tracker.snapshot()["llm_call_count"], 8)

    def test_medium_complex_path_skips_llm_critic_after_deterministic_checks(self):
        df = pd.DataFrame(
            {"Year": ["2019", "2020", "2021"], "Profit": [5, 10, 30]}
        )
        fake = FakePipelineLLM(
            semantic_score=0.75,
            rows=["2020", "2021"],
            cols=["Profit"],
        )
        pipeline, _ = self._pipeline(fake)
        state = TQASessionState(
            question="How much did Profit increase from 2020 to 2021?",
            df=df,
            table_schema=_build_table_schema(df),
            answer_contract=infer_answer_contract(
                "How much did Profit increase from 2020 to 2021?",
                reasoning_required=True,
            ),
        )

        result = pipeline.run(state)

        self.assertEqual(result.difficulty_level, "medium")
        self.assertTrue(result.critic_skipped)
        self.assertEqual(result.critic_verdict, "PASS")
        self.assertFalse(any("careful auditor" in prompt for prompt in fake.prompts))

    def test_true_false_task_routes_compresses_and_classifies_without_planner(self):
        df = pd.DataFrame(
            {
                "Country": ["Italy", "France"],
                "Winner": ["yes", "no"],
            }
        )
        fake = FakePipelineLLM(
            semantic_score=0.8,
            rows=["Italy"],
            cols=["Country", "Winner"],
            classification_output='{"label":"true"}',
        )
        pipeline, tracker = self._pipeline(fake)
        state = TQASessionState(
            question="The winning country was Italy.",
            df=df,
            table_schema=_build_table_schema(df),
            answer_mode="true_false",
        )

        result = pipeline.run(state)

        self.assertEqual(result.final_value, "true")
        self.assertEqual(result.final_answer, "true")
        self.assertTrue(result.exec_success)
        self.assertEqual(result.planner_raw_output, "")
        self.assertEqual(tracker.snapshot()["llm_call_count"], 3)

    def test_numeric_true_false_task_uses_code_reasoning(self):
        df = pd.DataFrame(
            {
                "Championship": list(range(14)),
                "Surface": ["grass", "grass", "grass"] + ["clay"] * 11,
            }
        )
        planner_output = (
            "[PLAN]\n"
            "Step1: Count grass rows and compare with 3.\n"
            "[CODE]\n"
            "grass_count = (df['Surface'].str.lower() == 'grass').sum()\n"
            "final_answer_value = bool(grass_count == 3)\n"
        )
        fake = FakePipelineLLM(
            semantic_score=0.8,
            rows=[],
            cols=["Surface"],
            classification_output='{"label":"false"}',
            planner_outputs=[planner_output],
        )
        pipeline, tracker = self._pipeline(fake)
        state = TQASessionState(
            question="Grass was the surface in 3 of 14 championships, or 21.43%.",
            df=df,
            table_schema=_build_table_schema(df),
            answer_mode="true_false",
        )

        result = pipeline.run(state)

        self.assertTrue(result.risk_escalated)
        self.assertNotEqual(result.planner_raw_output, "")
        self.assertEqual(result.classification_raw_output, "")
        self.assertEqual(result.final_value, "true")
        self.assertEqual(result.contract_validation, {"valid": True, "reason": ""})
        self.assertGreaterEqual(tracker.snapshot()["llm_call_count"], 3)

    def test_tabfact_verifier_can_correct_code_label_using_all_clauses(self):
        df = pd.DataFrame(
            {"place": ["t7"], "player": ["peter"], "prize": [9000]}
        )
        planner_output = (
            "[PLAN]\nStep1: Check the place only.\n[CODE]\n"
            "final_answer_value = df.loc[0, 'place'] == 't7'\n"
        )
        fake = FakePipelineLLM(
            semantic_score=0.8,
            rows=[],
            cols=["place", "player", "prize"],
            planner_outputs=[planner_output],
            verification_output='{"label":"false"}',
        )
        pipeline, _ = self._pipeline(fake)
        contract = infer_answer_contract(
            "T7 was Peter's place and his prize was 10875.",
            "true_false",
            reasoning_required=True,
        )
        state = TQASessionState(
            question="T7 was Peter's place and his prize was 10875.",
            df=df,
            table_schema=_build_table_schema(df),
            answer_mode="true_false",
            answer_contract=contract,
            dataset_profile="tabfact",
            dataset_instructions="Verify every clause.",
        )

        result = pipeline.run(state)

        self.assertEqual(result.final_value, "false")
        self.assertTrue(any("TabFact verification judge" in p for p in fake.prompts))

    def test_ungrounded_unique_count_replans_before_execution(self):
        df = pd.DataFrame(
            {
                "Championship": ["A", "A", "B", "C"],
                "Surface": ["grass", "clay", "grass", "grass"],
            }
        )
        wrong = (
            "[PLAN]\nStep1: Count unique values.\n[CODE]\n"
            "total = df['Championship'].nunique()\n"
            "final_answer_value = total == 4\n"
        )
        corrected = (
            "[PLAN]\nStep1: Count rows.\n[CODE]\n"
            "total = len(df)\n"
            "grass = (df['Surface'] == 'grass').sum()\n"
            "final_answer_value = total == 4 and grass == 3\n"
        )
        fake = FakePipelineLLM(
            semantic_score=0.8,
            rows=[],
            cols=["Surface"],
            planner_outputs=[wrong, corrected],
        )
        pipeline, _ = self._pipeline(fake)
        state = TQASessionState(
            question="Grass was the surface in 3 of the 4 championships.",
            df=df,
            table_schema=_build_table_schema(df),
            answer_mode="true_false",
        )

        result = pipeline.run(state)

        self.assertEqual(result.final_value, "true")
        self.assertEqual(result.grounding_validation, {"valid": True, "reason": ""})
        planner_prompts = [p for p in fake.prompts if "table reasoning planner" in p]
        self.assertEqual(len(planner_prompts), 2)
        self.assertIn("Use row counts", planner_prompts[1])

    def test_contract_mismatch_replans_entity_list(self):
        df = pd.DataFrame(
            {
                "Team": ["Alpha", "Beta", "Gamma"],
                "Races": [13, 14, 10],
            }
        )
        wrong_shape = (
            "[PLAN]\nStep1: Count qualifying teams.\n[CODE]\n"
            "final_answer_value = int((df['Races'] >= 13).sum())\n"
        )
        corrected_shape = (
            "[PLAN]\nStep1: Return qualifying team names.\n[CODE]\n"
            "final_answer_value = df.loc[df['Races'] >= 13, 'Team'].tolist()\n"
        )
        fake = FakePipelineLLM(
            semantic_score=0.8,
            rows=[],
            cols=["Team", "Races"],
            planner_outputs=[wrong_shape, corrected_shape],
        )
        pipeline, _ = self._pipeline(fake)
        state = TQASessionState(
            question="Which teams raced at least 13 races?",
            df=df,
            table_schema=_build_table_schema(df),
        )

        result = pipeline.run(state)

        planner_prompts = [
            prompt for prompt in fake.prompts if "table reasoning planner" in prompt
        ]
        self.assertEqual(len(planner_prompts), 2)
        self.assertEqual(result.final_value, ["Alpha", "Beta"])
        self.assertEqual(result.contract_validation, {"valid": True, "reason": ""})
        self.assertIn("requires a non-empty list", planner_prompts[1])

    def test_execution_error_is_preserved_for_replan_feedback(self):
        df = pd.DataFrame({"Position": [1, 2, 3]})
        failing = (
            "[PLAN]\nStep1: Inspect the positions.\n[CODE]\n"
            "final_answer_value = int(df['Position'].str.startswith('Lord').sum())\n"
        )
        corrected = (
            "[PLAN]\nStep1: Count the listed positions.\n[CODE]\n"
            "final_answer_value = int(df['Position'].notna().sum())\n"
        )
        fake = FakePipelineLLM(
            semantic_score=0.8,
            rows=[],
            cols=["Position"],
            planner_outputs=[failing, corrected],
        )
        pipeline, _ = self._pipeline(fake)
        state = TQASessionState(
            question="How many positions are listed?",
            df=df,
            table_schema=_build_table_schema(df),
        )

        result = pipeline.run(state)

        planner_prompts = [
            prompt for prompt in fake.prompts if "table reasoning planner" in prompt
        ]
        self.assertEqual(len(planner_prompts), 2)
        self.assertIn("Execution failed:", planner_prompts[1])
        self.assertIn(".str accessor", planner_prompts[1])
        self.assertIn("verify that filters match", planner_prompts[1])
        self.assertEqual(result.final_value, 3)

    def test_yes_no_task_normalizes_verbose_label(self):
        df = pd.DataFrame({"Event": ["A", "B"], "Acts": [5, 60]})
        fake = FakePipelineLLM(
            semantic_score=0.8,
            rows=["A", "B"],
            cols=["Event", "Acts"],
            classification_output="The answer is No.",
        )
        pipeline, _ = self._pipeline(fake)
        state = TQASessionState(
            question="Are the event sizes similar?",
            df=df,
            table_schema=_build_table_schema(df),
            answer_mode="yes_no",
        )

        result = pipeline.run(state)

        self.assertEqual(result.final_value, "No")
        self.assertEqual(result.final_answer, "No")

    def test_comparison_label_task_uses_declared_closed_set(self):
        df = pd.DataFrame({"Year": [2020, 2021], "Score": [5, 8]})
        planner_output = (
            "[PLAN]\nStep1: Compare the first and last score.\n[CODE]\n"
            "final_answer_value = 'better' if df['Score'].iloc[-1] > "
            "df['Score'].iloc[0] else 'worse'\n"
        )
        fake = FakePipelineLLM(
            semantic_score=0.8,
            rows=["2020", "2021"],
            cols=["Score"],
            classification_output='{"label":"worse"}',
            planner_outputs=[planner_output],
        )
        pipeline, _ = self._pipeline(fake)
        state = TQASessionState(
            question="Did performance get better, worse, or stay equal?",
            df=df,
            table_schema=_build_table_schema(df),
            answer_mode="better_worse_equal",
        )

        result = pipeline.run(state)

        self.assertTrue(result.risk_escalated)
        self.assertEqual(result.classification_raw_output, "")
        self.assertEqual(result.final_value, "better")
        self.assertEqual(result.final_answer, "better")

    def test_crt_duration_shortcut_treats_24_hours_as_one_day(self):
        df = pd.DataFrame(
            {
                "year": [1990, 1991, 1992],
                "event": ["a", "b", "c"],
                "days": ["1 day", "24 hours", "1 day"],
            }
        )
        fake = FakePipelineLLM(semantic_score=0.8, rows=["1990", "1991"], cols=["days"])
        pipeline, _ = self._pipeline(fake)
        state = TQASessionState(
            question=(
                "Has the duration of festivals held at Donington Park changed over time? "
                "Answer with only 'Yes' or 'No' that is most accurate and nothing else."
            ),
            df=df,
            table_schema=_build_table_schema(df),
            dataset_profile="crt",
        )

        result = pipeline.run(state)

        self.assertEqual(result.final_value, "No")
        self.assertEqual(result.final_answer, "No")
        self.assertNotIn("table reasoning planner", "\n".join(fake.prompts))

    def test_crt_event_type_difference_shortcut_requires_systematic_mapping(self):
        df = pd.DataFrame(
            {
                "event": ["monsters of rock", "one step beyond", "monsters of rock"],
                "days": ["1 day", "24 hours", "1 day"],
                "stages": ["1 stage", "1 stage", "2 stages"],
            }
        )
        fake = FakePipelineLLM(semantic_score=0.8, rows=["monsters"], cols=["event"])
        pipeline, _ = self._pipeline(fake)
        state = TQASessionState(
            question=(
                "Is there a difference in the types of events hosted at Donington Park "
                "based on the number of stages or days they have? Answer with only 'Yes' "
                "or 'No' that is most accurate and nothing else."
            ),
            df=df,
            table_schema=_build_table_schema(df),
            dataset_profile="crt",
        )

        result = pipeline.run(state)

        self.assertEqual(result.final_value, "No")
        self.assertEqual(result.final_answer, "No")
        self.assertNotIn("table reasoning planner", "\n".join(fake.prompts))

    def test_crt_percentage_snapshot_shortcut_averages_requested_cells(self):
        df = pd.DataFrame(
            {
                "rank": [1, 2, 3, 4, 5, "sum"],
                "county": ["oslo", "akershus", "hordaland", "rogaland", "sor", "norway"],
                "% (1960)": [13.2, 6.3, 9.4, 6.6, 5.8, 100.0],
                "% (2000)": [11.3, 10.4, 9.7, 8.3, 5.8, 100.0],
                "% (2040)": [12.8, 11.9, 10.2, 9.9, 6.0, 100.0],
            }
        )
        fake = FakePipelineLLM(semantic_score=0.8, rows=["1", "2"], cols=["% (1960)"])
        pipeline, _ = self._pipeline(fake)
        state = TQASessionState(
            question=(
                "What is the average percentage change in population for the top 5 "
                "ranked Norwegian counties between 1960 and 2040?"
            ),
            df=df,
            table_schema=_build_table_schema(df),
            dataset_profile="crt",
        )

        result = pipeline.run(state)

        self.assertEqual(result.final_value, 9.173)
        self.assertEqual(result.final_answer, "9.173")
        self.assertNotIn("table reasoning planner", "\n".join(fake.prompts))

    def test_wtq_last_character_question_returns_last_row_entity(self):
        df = pd.DataFrame(
            {
                "position": [1, 2, 3],
                "character": ["alpha", "bravo", "diams"],
                "actor": ["a", "b", "c"],
            }
        )
        fake = FakePipelineLLM(
            semantic_score=0.05,
            rows=["diams"],
            cols=["character"],
            direct_answer_output='{"answer":"le"}',
        )
        pipeline, _ = self._pipeline(fake)
        state = TQASessionState(
            question="What is the name of the last character in the table?",
            df=df,
            table_schema=_build_table_schema(df),
            dataset_profile="wtq",
        )

        result = pipeline.run(state)

        self.assertEqual(result.final_value, "diams")
        self.assertEqual(result.final_answer, "diams")
        self.assertNotIn("direct table QA extractor", "\n".join(fake.prompts))

    def test_wtq_last_on_chart_shortcut_returns_target_entity_column(self):
        df = pd.DataFrame(
            {
                "Event": ["100 m", "200 m", "4x400 m relay"],
                "Time": ["10.20", "20.50", "One hour"],
            }
        )

        value = TableQAPipeline._wtq_last_row_entity_answer(
            "which event is last on the chart",
            df,
        )

        self.assertEqual(value, "4x400 m relay")

    def test_wtq_superlative_owner_shortcut_excludes_election_summary_rows(self):
        df = pd.DataFrame(
            {
                "Party": ["Conservative", "Labour", "Majority", "Turnout"],
                "Candidate": ["Patrick McLoughlin", "Stephen Clamp", "Majority", "Turnout"],
                "Votes": ["24,280", "16,910", "7,370", "50,589"],
            }
        )

        value = TableQAPipeline._wtq_superlative_owner_answer(
            "which candidate has the most votes?",
            df,
        )

        self.assertEqual(value, "Patrick McLoughlin")

    def test_wtq_superlative_owner_shortcut_returns_entity_for_last_opened(self):
        df = pd.DataFrame(
            {
                "Stadium": ["Stade de France", "Allianz Riviera", "Stade Chaban-Delmas"],
                "Opened": ["1998", "2013", "1938"],
            }
        )

        value = TableQAPipeline._wtq_superlative_owner_answer(
            "which stadium was the last to be opened?",
            df,
        )

        self.assertEqual(value, "Allianz Riviera")

    def test_wtq_superlative_owner_shortcut_uses_title_column_for_album_sales(self):
        df = pd.DataFrame(
            {
                "Title": ["The Remixes", "The Remixes II"],
                "Album details": ["Released: 1997", "Released: 1998"],
                "Sales": [640000, 300000],
            }
        )

        value = TableQAPipeline._wtq_superlative_owner_answer(
            "which album has the most sales?",
            df,
        )

        self.assertEqual(value, "The Remixes")

    def test_wtq_superlative_owner_shortcut_restricts_explicit_or_candidates(self):
        df = pd.DataFrame(
            {
                "Island": ["Mljet", "Ærø", "Tiree", "Kasos"],
                "Area (km²)": [100, 88, 78, 66],
            }
        )

        value = TableQAPipeline._wtq_superlative_owner_answer(
            "which island has the most area, tiree or kasos?",
            df,
        )

        self.assertEqual(value, "Tiree")

    def test_wtq_after_reference_shortcut_counts_following_rows(self):
        df = pd.DataFrame(
            {
                "#": list(range(1, 7)),
                "Title": ["Intro", "Seven", "Rollin Hard", "Harvest", "Sippin", "Red Mist"],
            }
        )

        value = TableQAPipeline._wtq_after_reference_answer(
            'how many song come after "rollin hard"?',
            df,
        )

        self.assertEqual(value, 3)

    def test_wtq_after_reference_shortcut_returns_next_entity_in_same_column(self):
        df = pd.DataFrame(
            {
                "Rank": ["1.", "1.", "1."],
                "Athlete": ["Andriy Sokolovskyy", "Stefan Holm", "Andrey Tereshin"],
                "2.15": ["o", "o", "o"],
                "Result": ["2.27", "2.27", "2.27"],
            }
        )

        value = TableQAPipeline._wtq_after_reference_answer(
            "who came in after stefan holm?",
            df,
        )

        self.assertEqual(value, "Andrey Tereshin")

    def test_wtq_after_reference_shortcut_handles_next_listed_entity_phrase(self):
        df = pd.DataFrame(
            {
                "Pos": [1, 2, 3],
                "Driver": ["Will Power", "Scott Dixon", "Mike Conway"],
            }
        )

        value = TableQAPipeline._wtq_after_reference_answer(
            "who is the next driver listed after scott dixon?",
            df,
        )

        self.assertEqual(value, "Mike Conway")

    def test_wtq_after_reference_shortcut_defaults_to_same_entity_column(self):
        df = pd.DataFrame(
            {
                "Atomic no.": [88, 89],
                "Name": ["Radium", "Actinium"],
                "Symbol": ["Ra", "Ac"],
            }
        )

        value = TableQAPipeline._wtq_after_reference_answer(
            "what element is after radium?",
            df,
        )

        self.assertEqual(value, "Actinium")

    def test_wtq_after_reference_shortcut_skips_blank_metadata_rows(self):
        df = pd.DataFrame(
            {
                "#": ["10", "", "11"],
                "Title": ["Like That", "", "Call It What You Want"],
                "Sample": ["Just Rhymin", "West Up", "Knucklehead"],
            }
        )

        value = TableQAPipeline._wtq_after_reference_answer(
            'which track comes after "like that"?',
            df,
        )

        self.assertEqual(value, "Call It What You Want")

    def test_wtq_route_after_stop_shortcut_uses_destination_order(self):
        df = pd.DataFrame(
            {
                "Route": ["10", "11"],
                "Destinations": [
                    "Amherstview Cataraqui Town Centre",
                    "Kingston Centre Cataraqui Town Centre",
                ],
                "Via": ["Collins Bay Road", "Bath Road Gardiners Town Centre"],
            }
        )

        value = TableQAPipeline._wtq_route_after_stop_answer(
            "where does the bus stop after kingston centre on route 11?",
            df,
        )

        self.assertEqual(value, "Cataraqui Town Centre")

    def test_wtq_zero_metric_shortcut_counts_rows_without_medals(self):
        df = pd.DataFrame(
            {
                "Nation": ["Tunisia", "Algeria", "Croatia", "Total"],
                "Silver": [0, 0, 3, 3],
            }
        )

        value = TableQAPipeline._wtq_zero_metric_count_answer(
            "how many countries did not win any silver medals?",
            df,
        )

        self.assertEqual(value, 2)

    def test_wtq_same_column_shortcut_counts_contained_matching_values(self):
        df = pd.DataFrame(
            {
                "Winner": ["A", "B", "C"],
                "Race leader": ["A", "B D", "D"],
            }
        )

        value = TableQAPipeline._wtq_same_column_count_answer(
            "how many times is the winner the same as the race leader?",
            df,
        )

        self.assertEqual(value, 2)

    def test_wtq_same_number_columns_shortcut_returns_requested_entity(self):
        df = pd.DataFrame(
            {
                "Eps #": ["5a", "11", "12"],
                "Prod #": ["1", "11", "13"],
                "Title": ["Klaws", "Danger In The Depths", "Return Of the Mole Man"],
            }
        )

        value = TableQAPipeline._wtq_same_number_entity_answer(
            "which episode has the same episode and production numbers?",
            df,
        )

        self.assertEqual(value, "Danger In The Depths")

    def test_wtq_same_number_as_reference_excludes_reference_entity(self):
        df = pd.DataFrame(
            {
                "Player": ["Greg Foster", "Kyrylo Fesenko", "John Wallace"],
                "No.": [42, 42, 44],
            }
        )

        value = TableQAPipeline._wtq_same_number_entity_answer(
            "who has the same number as greg foster?",
            df,
        )

        self.assertEqual(value, "Kyrylo Fesenko")

    def test_wtq_contributor_shortcut_allows_single_edit_name_typo(self):
        df = pd.DataFrame(
            {
                "Title": ["Song A", "Song B", "Song C"],
                "Lyricist": ["Shailendra", "Hasrat Jaipuri", "Shailendra"],
            }
        )

        value = TableQAPipeline._wtq_contributor_count_answer(
            "how many songs on this soundtrack did shailenra contribute to?",
            df,
        )

        self.assertEqual(value, 2)

    def test_wtq_top_placing_competitor_shortcut_uses_lowest_rank(self):
        df = pd.DataFrame({"Place": [2, "Semifinal (1st)"], "Competitor": ["Runner B", "Runner A"]})

        value = TableQAPipeline._wtq_top_placing_competitor_answer(
            "who was the top placing competitor?",
            df,
        )

        self.assertEqual(value, "Runner A")

    def test_wtq_duration_shortcut_returns_elapsed_minutes(self):
        df = pd.DataFrame(
            {
                "Departure": ["11.34"],
                "Arrival": ["12.05"],
                "Going to": ["Grantham"],
            }
        )

        value = TableQAPipeline._wtq_duration_answer(
            "how long does it take to get to grantham when departing at 11.34?",
            df,
        )

        self.assertEqual(value, "31 minutes")

    def test_wtq_career_duration_shortcut_returns_year_span(self):
        df = pd.DataFrame(
            {
                "Player": ["Derek Fisher", "Greg Foster"],
                "Years for Jazz": ["2006-2007", "1995-99"],
            }
        )

        value = TableQAPipeline._wtq_named_year_span_duration_answer(
            "how long did derek fisher's career last?",
            df,
        )

        self.assertEqual(value, "1 year")

    def test_wtq_consecutive_month_shortcut_counts_longest_run(self):
        df = pd.DataFrame(
            {
                "Season": [1, 2, 3, 4, 5, 6, 7],
                "Season Premiere": [
                    "March 4, 2006",
                    "October 7, 2006",
                    "October 15, 2007",
                    "October 13, 2008",
                    "October 12, 2009",
                    "September 6, 2010",
                    "October 29, 2013",
                ],
            }
        )

        value = TableQAPipeline._wtq_consecutive_month_count_answer(
            "how many consecutive seasons premiered in october?",
            df,
        )

        self.assertEqual(value, 4)

    def test_wtq_after_month_shortcut_counts_rows_after_requested_month(self):
        df = pd.DataFrame(
            {
                "Date": ["10", "11", "12", "13", "14", "15", "16"],
                "Rnd": [
                    "August 5",
                    "August 26",
                    "September 2",
                    "September 16",
                    "September 23",
                    "October 7",
                    "October 21",
                ],
                "Race Name": ["A", "B", "C", "D", "E", "F", "G"],
            }
        )

        value = TableQAPipeline._wtq_after_month_row_count_answer(
            "how many races took place after august?",
            df,
        )

        self.assertEqual(value, 5)

    def test_wtq_after_month_shortcut_ignores_specific_day_cutoff(self):
        df = pd.DataFrame(
            {
                "Date": ["October 1", "October 2", "November 1"],
                "Game": ["A", "B", "C"],
            }
        )

        value = TableQAPipeline._wtq_after_month_row_count_answer(
            "how many games were played after october 1st?",
            df,
        )

        self.assertIsNone(value)

    def test_wtq_date_cutoff_shortcut_counts_rows_after_specific_date_by_table_order(self):
        df = pd.DataFrame(
            {
                "Date": ["September 20", "October 4", "October 25", "February 13"],
                "Team": ["A", "B", "C", "D"],
            }
        )

        value = TableQAPipeline._wtq_date_cutoff_row_count_answer(
            "how many games were played after october 1st?",
            df,
        )

        self.assertEqual(value, 3)

    def test_wtq_date_cutoff_shortcut_counts_rows_before_specific_date(self):
        df = pd.DataFrame(
            {
                "Original air date": ["Writer Name", "November 17, 1965", "December 1, 1965", "December 8, 1965"],
                "Prod. code": ["September 15, 1965 101", "110", "111", "112"],
                "Episode": ["A", "B", "C", "D"],
            }
        )

        value = TableQAPipeline._wtq_date_cutoff_row_count_answer(
            "how many episodes aired before december 1st, 1965?",
            df,
        )

        self.assertEqual(value, 2)

    def test_wtq_first_metric_threshold_date_shortcut_returns_first_crossing_date(self):
        df = pd.DataFrame(
            {
                "Date introduced": ["16 August 2004", "14 June 2005"],
                "Class 1 (e.g. Motorbike)": ["£2.00", "£2.50"],
            }
        )

        value = TableQAPipeline._wtq_first_metric_threshold_date_answer(
            "on what date did the toll for class 1 first go above 2.00?",
            df,
        )

        self.assertEqual(value, "14 June 2005")

    def test_wtq_combined_numbers_shortcut_sums_requested_metric(self):
        df = pd.DataFrame(
            {
                "Season": [2008, 2009, 2010, 2011],
                "Super G": [46, 16, 6, 99],
                "Combined": [31, 1, 2, 3],
            }
        )

        value = TableQAPipeline._wtq_combined_numbers_for_column_answer(
            "before 2011 whats the combined numbers for super g?",
            df,
        )

        self.assertEqual(value, 68)

    def test_wtq_total_metric_shortcut_prefers_existing_total_row(self):
        df = pd.DataFrame(
            {
                "Season": ["2008/09", "2009/10", ""],
                "Club": ["Excelsior Mouscron", "Excelsior Mouscron", ""],
                "Competition": ["Jupiler League", "Jupiler League", "Totaal"],
                "Games": ["31", "14", "278"],
                "Goals": ["1", "1", "4"],
            }
        )

        value = TableQAPipeline._wtq_existing_total_metric_answer(
            "what is the total number of goals?",
            df,
        )

        self.assertEqual(value, 4)

    def test_wtq_how_long_roster_count_keeps_numeric_count(self):
        df = pd.DataFrame({"Player": list("ABC"), "Date": ["1 January 2010"] * 3})

        value = _canonicalize_wtq_scalar(
            3,
            df,
            "how long is the roster for the 2010 woodlands wellington fc season?",
        )

        self.assertEqual(value, 3)

    def test_wtq_only_metric_value_shortcut_returns_entity(self):
        df = pd.DataFrame(
            {
                "Pos": [1, 2, 3],
                "Name": ["Justin Wilson", "Sébastien Bourdais", "Jan Heylen"],
                "Grid": [2, 1, 7],
            }
        )

        value = TableQAPipeline._wtq_only_metric_value_answer(
            "the only grid with 1",
            df,
        )

        self.assertEqual(value, "Sébastien Bourdais")

    def test_wtq_multi_condition_lookup_shortcut_returns_requested_column(self):
        df = pd.DataFrame(
            {
                "Season": ["2012", "2013", "2014"],
                "Age": ["25", "26", "27"],
                "Overall": ["24", "48", "18"],
                "Giant Slalom": ["16", "48", "25"],
            }
        )
        fake = FakePipelineLLM(semantic_score=0.9, rows=["2013"], cols=["Age", "Overall", "Giant Slalom"])
        pipeline, _ = self._pipeline(fake)
        state = TQASessionState(
            question="at which age was the overall as 48 and the giant slalom 48, too?",
            df=df,
            table_schema=_build_table_schema(df),
            dataset_profile="wtq",
        )

        self.assertTrue(pipeline._try_wtq_semantic_shortcut(state))
        self.assertEqual(state.final_value, "26")

    def test_wtq_listed_after_cell_shortcut_reads_row_major_next_year(self):
        df = pd.DataFrame(
            {
                "May 20-21 118": ["May 20, 2012"],
                "March 9 120": ["March 9, 2016"],
                "December 25-26 122": ["December 26, 2019"],
            }
        )

        value = TableQAPipeline._wtq_listed_after_cell_answer(
            "what is the year listed after 2012?",
            df,
        )

        self.assertEqual(value, "2016")

    def test_wtq_listed_after_cell_shortcut_returns_next_row_requested_column(self):
        df = pd.DataFrame(
            {
                "#": ["4", "5"],
                "Stadium": ["Parc des Princes", "Stade Félix Bollaert"],
                "Capacity": ["48,712", "41,233"],
            }
        )

        value = TableQAPipeline._wtq_listed_after_cell_answer(
            "what is the next stadium listed after parc des princes?",
            df,
        )

        self.assertEqual(value, "Stade Félix Bollaert")

    def test_wtq_directly_before_shortcut_returns_requested_number_column(self):
        df = pd.DataFrame(
            {
                "Num": ["009", "010"],
                "Nickname": ["Pop", "Felix"],
                "Episode": ["Leroy & Stitch", "Snafu"],
            }
        )
        fake = FakePipelineLLM(semantic_score=0.9, rows=["Felix"], cols=["Num", "Nickname"])
        pipeline, _ = self._pipeline(fake)
        state = TQASessionState(
            question="which experiment number came directly before felix?",
            df=df,
            table_schema=_build_table_schema(df),
            dataset_profile="wtq",
        )

        self.assertTrue(pipeline._try_wtq_semantic_shortcut(state))
        self.assertEqual(state.final_value, "009")

    def test_wtq_overtime_count_shortcut_counts_explicit_ot_markers_between_teams(self):
        df = pd.DataFrame(
            {
                "Winner": ["New York Giants", "Philadelphia Eagles", "New York Giants"],
                "Result": ["26-23 (OT)", "36-22", "30-24 (OT)"],
                "Loser": ["Philadelphia Eagles", "New York Giants", "Philadelphia Eagles"],
            }
        )
        fake = FakePipelineLLM(semantic_score=0.9, rows=["Eagles"], cols=["Result"])
        pipeline, _ = self._pipeline(fake)
        state = TQASessionState(
            question="what is the number of times a game went into overtime between the eagles and giants?",
            df=df,
            table_schema=_build_table_schema(df),
            dataset_profile="wtq",
        )

        self.assertTrue(pipeline._try_wtq_semantic_shortcut(state))
        self.assertEqual(state.final_value, 2)

    def test_wtq_who_answer_canonicalization_strips_country_suffix(self):
        df = pd.DataFrame(
            {
                "Event": ["Slalom"],
                "Bronze": ["Ludwig Wolf Germany (GER)"],
            }
        )

        value = _canonicalize_wtq_scalar(
            "Ludwig Wolf Germany (GER)",
            df,
            "who is the last person listed under slalom?",
        )

        self.assertEqual(value, "Ludwig Wolf")

    def test_wtq_playoff_count_shortcut_excludes_parenthetical_no_playoff(self):
        df = pd.DataFrame(
            {
                "Year": ["1934/35", "1936/37", "1945/46", "1948/49"],
                "Playoffs": ["No playoff", "1st Round", "Champion (no playoff)", "N/A"],
            }
        )
        fake = FakePipelineLLM(semantic_score=0.9, rows=["Playoffs"], cols=["Playoffs"])
        pipeline, _ = self._pipeline(fake)
        state = TQASessionState(
            question="how many years did the true american club make the playoff?",
            df=df,
            table_schema=_build_table_schema(df),
            dataset_profile="wtq",
        )

        self.assertTrue(pipeline._try_wtq_semantic_shortcut(state))
        self.assertEqual(state.final_value, 1)

    def test_wtq_division_winner_count_shortcut_counts_nonempty_column_entries(self):
        df = pd.DataFrame(
            {
                "Year": list(range(2012, 2000, -1)),
                "Community Division": [
                    "",
                    "Brothers in Arms",
                    "Gap Angels",
                    "",
                    "Tangentyere",
                    "Cooktown",
                    "Cat Tigers",
                    "Melville Island",
                    "Alkupitja",
                    "Normanton",
                    "",
                    "",
                ],
            }
        )
        fake = FakePipelineLLM(semantic_score=0.9, rows=["Community Division"], cols=["Community Division"])
        pipeline, _ = self._pipeline(fake)
        state = TQASessionState(
            question="what is the number of winners in the community division?",
            df=df,
            table_schema=_build_table_schema(df),
            dataset_profile="wtq",
        )

        self.assertTrue(pipeline._try_wtq_semantic_shortcut(state))
        self.assertEqual(state.final_value, 8)

    def test_wtq_sponsor_count_shortcut_counts_unique_sponsor_names_across_sponsor_columns(self):
        df = pd.DataFrame(
            {
                "Year": [str(year) for year in range(1, 18)],
                "Shirt Sponsor": [
                    "National Express",
                    "",
                    "Whitbread",
                    "Duraflex",
                    "Gulf Oil",
                    "Gulf Oil",
                    "Gulf Oil",
                    "Empress",
                    "Empress",
                    "Endsleigh Insurance",
                    "Endsleigh Insurance",
                    "Towergate Insurance",
                    "Bence Building Merchants",
                    "Mira Showers",
                    "Mira Showers",
                    "Mira Showers",
                    "Mira Showers",
                ],
                "Back of Shirt Sponsor": [""] * 14
                + ["PSU Technology Group", "Barr Stadia", "Gloucestershire College"],
                "Short Sponsor": [""] * 15 + ["Gloucestershire Echo", "Gloucestershire Echo"],
            }
        )
        fake = FakePipelineLLM(semantic_score=0.9, rows=["Sponsor"], cols=["Shirt Sponsor"])
        pipeline, _ = self._pipeline(fake)
        state = TQASessionState(
            question="total number of sponsors?",
            df=df,
            table_schema=_build_table_schema(df),
            dataset_profile="wtq",
        )

        self.assertTrue(pipeline._try_wtq_semantic_shortcut(state))
        self.assertEqual(state.final_value, 13)

    def test_wtq_retired_injured_after_ordinal_attempt_shortcut_matches_third_attempt(self):
        df = pd.DataFrame(
            {
                "Name": ["Esther Shahamorov", "Yossef Romano"],
                "Sport": ["Athletics", "Weightlifting"],
                "Performance": [
                    "Did not start",
                    "(retired injured on third attempt to press 137.5kg)",
                ],
            }
        )
        fake = FakePipelineLLM(semantic_score=0.9, rows=["injured"], cols=["Name", "Performance"])
        pipeline, _ = self._pipeline(fake)
        state = TQASessionState(
            question="which person retired injured after three attempts in their event?",
            df=df,
            table_schema=_build_table_schema(df),
            dataset_profile="wtq",
        )

        self.assertTrue(pipeline._try_wtq_semantic_shortcut(state))
        self.assertEqual(state.final_value, "Yossef Romano")

    def test_wtq_usage_count_shortcut_counts_items_in_matching_usage_cell(self):
        df = pd.DataFrame(
            {
                "Common name": ["Arjun"],
                "Characteristics, Usage and Status": [
                    "It is heavy and strong. It has such uses as beams, rafters, and posts."
                ],
            }
        )

        value = TableQAPipeline._wtq_usage_count_answer(
            "how many uses are listed for the arjun tree?",
            df,
        )

        self.assertEqual(value, 3)

    def test_wtq_occurrence_count_shortcut_counts_mentions_across_cells(self):
        df = pd.DataFrame(
            {
                "Band": ["Gary Numan"],
                "Image": [
                    "Guns N' Roses Sweet Child o' Mine; Guns N' Roses Paradise City; Guns N' Roses Nightrain"
                ],
            }
        )

        value = TableQAPipeline._wtq_occurrence_count_answer(
            "how many times is guns n' roses listed?",
            df,
        )

        self.assertEqual(value, 3)

    def test_wtq_ordinal_position_count_shortcut_includes_parenthesized_qualifiers(self):
        df = pd.DataFrame({"Position": ["13th (q)", "7th", "10th (q)", "11th", "3rd"]})

        value = TableQAPipeline._wtq_ordinal_position_count_answer(
            "how many times was a position of at least 10th place or better earned?",
            df,
        )

        self.assertEqual(value, 3)

    def test_wtq_extreme_metric_lookup_shortcut_returns_requested_column(self):
        df = pd.DataFrame(
            {
                "Crater": ["B", "T"],
                "Latitude": ["9.0° N", "7.0° N"],
                "Diameter": ["62 km", "15 km"],
            }
        )

        value = TableQAPipeline._wtq_extreme_metric_lookup_answer(
            "what is the latitude of the crater with the smallest diameter?",
            df,
        )

        self.assertEqual(value, "7.0° N")

    def test_wtq_score_pair_single_point_shortcut_returns_low_score_entity(self):
        df = pd.DataFrame(
            {
                "Winner": ["John Higgins", "Stephen Hendry"],
                "Runner-up": ["John Parrott", "Tony Drago"],
                "Score": ["9-5", "9-1"],
            }
        )

        value = TableQAPipeline._wtq_score_pair_low_score_entity_answer(
            "which player scored only one point in a tournament?",
            df,
        )

        self.assertEqual(value, "Tony Drago")

    def test_wtq_column_header_year_shortcut_returns_series_number(self):
        df = pd.DataFrame(
            {
                "May 20-21 118": ["May 21, 1993", "128"],
                "March 9 120": ["March 9, 1997", "130"],
            }
        )

        value = TableQAPipeline._wtq_column_header_number_for_year_answer(
            "what is the series number of the only eclipse in 1993?",
            df,
        )

        self.assertEqual(value, "118")

    def test_wtq_rank_gap_shortcut_returns_absolute_difference(self):
        df = pd.DataFrame(
            {
                "Position": [1, 2, 3],
                "Officer": ["Lord High Steward", "Lord High Chancellor", "Lord High Treasurer"],
            }
        )

        value = TableQAPipeline._wtq_rank_gap_answer(
            "how many ranks about lord high treasurer is the lord high steward?",
            df,
        )

        self.assertEqual(value, 2)

    def test_wtq_explicit_option_absence_shortcut_returns_missing_option(self):
        df = pd.DataFrame(
            {
                "Competition": [
                    "World Indoor Championships",
                    "World Indoor Championships",
                    "World Athletics Final",
                ],
                "Venue": ["Paris, France", "Maebashi, Japan", "Monaco"],
            }
        )

        value = TableQAPipeline._wtq_explicit_option_absence_answer(
            "which of the following countries did melissa morrison not compete at; france, japan, monaco, orcanada?",
            df,
        )

        self.assertEqual(value, "canada")

    def test_wtq_metric_value_entity_list_shortcut_returns_all_matching_entities(self):
        df = pd.DataFrame(
            {
                "Team": ["Keflavík", "Fram", "Leiftur"],
                "Draw": [7, 5, 7],
            }
        )

        value = TableQAPipeline._wtq_metric_value_entity_list_answer(
            "only 2 teams had 7 draws, who were they?",
            df,
        )

        self.assertEqual(value, ["Keflavík", "Leiftur"])

    def test_wtq_threshold_count_shortcut_treats_or_below_as_strict_below(self):
        df = pd.DataFrame(
            {
                "Location": ["A", "B", "C"],
                "Gross Capacity (MWe)": [800, 1000, 1300],
            }
        )

        value = TableQAPipeline._wtq_threshold_count_answer(
            "how many reactors only have a gross capacity of 1000 or below?",
            df,
        )

        self.assertEqual(value, 1)

    def test_wtq_only_metric_value_shortcut_handles_to_have_phrase(self):
        df = pd.DataFrame(
            {
                "Name": ["Annie Penn Hospital", "Vidant Bertie Hospital"],
                "Hospital beds": ["110", "6"],
            }
        )

        value = TableQAPipeline._wtq_only_metric_value_answer(
            "what is the only hospital to have 6 hospital beds?",
            df,
        )

        self.assertEqual(value, "Vidant Bertie Hospital")

    def test_wtq_metric_sum_by_entity_shortcut_sums_country_rows(self):
        df = pd.DataFrame(
            {
                "Rider": ["Sylvain Geboers", "Roger De Coster", "Adolf Weil"],
                "Country": ["Belgium", "Belgium", "Germany"],
                "Wins": ["3", "3", "2"],
            }
        )

        value = TableQAPipeline._wtq_metric_sum_by_entity_answer(
            "total wins by belgian riders",
            df,
        )

        self.assertEqual(value, 6)

    def test_wtq_metric_sum_by_entity_shortcut_sums_owner_rows(self):
        df = pd.DataFrame(
            {
                "Network name": ["Azteca 7", "TV 10 Chiapas", "Azteca 13", "Canal 5"],
                "Owner": ["TV Azteca", "Gobierno del Estado de Chiapas", "TV Azteca", "Televisa"],
                "Affiliates": ["5", "7", "4", "4"],
            }
        )

        value = TableQAPipeline._wtq_metric_sum_by_entity_answer(
            "how many affiliates does tv azteca have all together?",
            df,
        )

        self.assertEqual(value, 9)

    def test_wtq_combined_entity_count_shortcut_counts_listed_entities(self):
        df = pd.DataFrame(
            {
                "Name": ["A", "B", "C", "D", "E"],
                "City or town": ["Barrington", "Farmington", "Rochester", "Rochester", "Dover"],
            }
        )

        value = TableQAPipeline._wtq_combined_entity_count_answer(
            "what is the number of listings from barrington, farmington, and rochester combined?",
            df,
        )

        self.assertEqual(value, 4)

    def test_wtq_column_value_count_shortcut_counts_phrase_matches(self):
        df = pd.DataFrame(
            {
                "Surface": ["Clay", "Hard", "Hard (i)", "Hard"],
                "Tournament": ["A", "B", "C", "D"],
            }
        )

        value = TableQAPipeline._wtq_column_value_count_answer(
            "how many hard surface courts are there?",
            df,
        )

        self.assertEqual(value, 3)

    def test_wtq_at_least_metric_shortcut_counts_rows_meeting_threshold(self):
        df = pd.DataFrame(
            {
                "Player": ["Aloisi", "Emerton", "Milicic", "Cahill"],
                "Friendlies": ["1", "-", "2", "-"],
                "Total Goals": ["5", "2", "2", "1"],
            }
        )

        value = TableQAPipeline._wtq_at_least_metric_count_answer(
            "number of players who scored at least 1 friendly",
            df,
        )

        self.assertEqual(value, 2)

    def test_wtq_inferred_blank_rank_shortcut_uses_row_order(self):
        df = pd.DataFrame(
            {
                "Rank": ["", "", "", "4"],
                "Name": ["Shani Davis", "Joey Cheek", "Erben Wennemars", "Lee Kyou-hyuk"],
            }
        )

        value = TableQAPipeline._wtq_inferred_rank_entity_answer(
            "shani davis is ranked number one, but who is ranked number three?",
            df,
        )

        self.assertEqual(value, "Erben Wennemars")

    def test_wtq_first_status_entity_shortcut_returns_first_evicted_person(self):
        df = pd.DataFrame(
            {
                "Celebrity": ["Winner", "Regina Do Santos", "Other"],
                "Status": ["Winner", "1st / 14th Evicted", "2nd Evicted"],
            }
        )

        value = TableQAPipeline._wtq_first_status_entity_answer(
            "who was the first person to get evicted?",
            df,
        )

        self.assertEqual(value, "Regina Do Santos")

    def test_wtq_stated_left_count_shortcut_subtracts_question_counts(self):
        value = TableQAPipeline._wtq_stated_left_count_answer(
            "there were seven keels laid before the month of july in 1918, two were expended as targets, how many were left?",
            pd.DataFrame({"Designation": ["PE-1"]}),
        )

        self.assertEqual(value, 5)

    def test_wtq_release_date_gap_shortcut_returns_month_difference(self):
        df = pd.DataFrame(
            {
                "Release date": ["February 2011", "June 2011"],
                "Album Title": ["I Love You", "Bida Best Hits Da Best"],
            }
        )

        value = TableQAPipeline._wtq_release_date_gap_answer(
            "how long were the release dates between bida best hits da best and i love you?",
            df,
        )

        self.assertEqual(value, "4 months")

    def test_wtq_only_column_threshold_shortcut_returns_row_label(self):
        df = pd.DataFrame(
            {
                "decimal128": [128, 12288],
                "Format": ["Total size (bits)", "Exponent range"],
            }
        )

        value = TableQAPipeline._wtq_only_column_threshold_answer(
            "name the only format with a decimal 128 value above 10,000.",
            df,
        )

        self.assertEqual(value, "Exponent range")

    def test_wtq_last_placing_entity_shortcut_uses_largest_rank(self):
        df = pd.DataFrame(
            {
                "Rank": ["1", "40"],
                "Diver": ["Winner", "Hsu Shi-Han"],
            }
        )

        value = TableQAPipeline._wtq_last_placing_entity_answer(
            "what diver came in last?",
            df,
        )

        self.assertEqual(value, "Hsu Shi-Han")

    def test_wtq_chart_threshold_shortcut_counts_unique_values(self):
        df = pd.DataFrame({"Builder": ["A", "B", "C", "C"]})

        value = TableQAPipeline._wtq_unique_count_threshold_answer(
            "are there at least 4 builders on the chart?",
            df,
        )

        self.assertEqual(value, "no")

    def test_wtq_frequency_and_last_column_shortcuts_are_deterministic(self):
        race_df = pd.DataFrame(
            {
                "Race": ["A", "B", "C", "D"],
                "Winning team": ["Team Penske", "Doug Shierson Racing", "Team Penske", "Newman/Haas"],
            }
        )
        history_df = pd.DataFrame(
            {
                "Year": [1972, 1973, 1996],
                "Team": ["Automobiles Ligier", "Automobiles Ligier", "Team Bigazzi SRL"],
            }
        )

        self.assertEqual(
            TableQAPipeline._wtq_no_more_than_once_answer(
                "which team(s)did not win more than once?",
                race_df,
            ),
            ["Doug Shierson Racing", "Newman/Haas"],
        )
        self.assertEqual(
            TableQAPipeline._wtq_last_requested_column_answer(
                "what was the last team that this racer was a part of at this race?",
                history_df,
            ),
            "Team Bigazzi SRL",
        )

    def test_wtq_last_requested_column_filters_year_qualifier(self):
        df = pd.DataFrame(
            {
                "Year": [2008, 2008, 2011],
                "Event": ["60 m", "200 m", "4x100 m"],
                "Notes": [6.81, 21.00, 40.15],
            }
        )

        value = TableQAPipeline._wtq_last_requested_column_answer(
            "what is the last note on 2008",
            df,
        )

        self.assertEqual(value, 21.00)

    def test_wtq_who_in_last_period_returns_requested_target_column(self):
        df = pd.DataFrame(
            {
                "Season": ["2010-11", "2011-12"],
                "League Top scorer": ["Player A", "Simon Makienok Christoffersen"],
            }
        )

        value = TableQAPipeline._wtq_last_requested_column_answer(
            "who was the top scorer in the last season?",
            df,
        )

        self.assertEqual(value, "Simon Makienok Christoffersen")

    def test_wtq_last_listed_owner_returns_requested_column(self):
        df = pd.DataFrame(
            {
                "Round": [1, 2, 10],
                "Date": ["May 21", "June 4", "November 5"],
                "Circuit": ["Sears Point", "Westwood", "Mexico City"],
            }
        )

        value = TableQAPipeline._wtq_last_requested_column_answer(
            "what is the date listed for the last round?",
            df,
        )

        self.assertEqual(value, "November 5")

    def test_tabfact_only_set_equality_shortcut_checks_unique_entities(self):
        df = pd.DataFrame(
            {
                "broadcaster": ["FOX", "CBS", "FOX", "CBS", "tba", "tba"],
                "series": ["NFL International Series"] * 6,
            }
        )
        fake = FakePipelineLLM(
            semantic_score=0.05,
            rows=["FOX"],
            cols=["broadcaster"],
            classification_output='{"label":"false"}',
        )
        pipeline, _ = self._pipeline(fake)
        state = TQASessionState(
            question="Only FOX and CBS have broadcast the NFL International Series.",
            df=df,
            table_schema=_build_table_schema(df),
            answer_mode="true_false",
            dataset_profile="tabfact",
        )

        result = pipeline.run(state)

        self.assertEqual(result.final_value, "true")
        self.assertEqual(result.final_answer, "true")
        self.assertNotIn("closed-label table classifier", "\n".join(fake.prompts))

    def test_tabfact_no_date_week_greater_shortcut_uses_normalized_date(self):
        df = pd.DataFrame(
            {
                "date": ["October 30, 1977", "November 6, 1977"],
                "week": [7, 8],
                "game": ["A", "B"],
            }
        )
        planner_output = (
            "[PLAN]\nStep1: Wrongly compare the week.\n[CODE]\n"
            "final_answer_value = False\n"
        )
        fake = FakePipelineLLM(
            semantic_score=0.8,
            rows=["October 30, 1977"],
            cols=["date", "week"],
            planner_outputs=[planner_output],
            verification_output='{"label":"false"}',
        )
        pipeline, _ = self._pipeline(fake)
        state = TQASessionState(
            question=(
                "There is no game that was played on October 30, 1977 that was "
                "listed greater than week 7."
            ),
            df=df,
            table_schema=_build_table_schema(df),
            answer_mode="true_false",
            dataset_profile="tabfact",
        )

        result = pipeline.run(state)

        self.assertEqual(result.final_value, "true")
        self.assertEqual(result.final_answer, "true")
        self.assertNotIn("table reasoning planner", "\n".join(fake.prompts))

    def test_tabfact_inverse_correlation_shortcut_accepts_negative_trend(self):
        df = pd.DataFrame(
            {
                "team": ["a", "b", "c", "d"],
                "games": [7, 7, 7, 7],
                "total points": [100, 90, 70, 55],
                "lost": [1, 2, 4, 6],
            }
        )
        fake = FakePipelineLLM(
            semantic_score=0.05,
            rows=[],
            cols=["total points", "games lost"],
            classification_output='{"label":"false"}',
        )
        pipeline, _ = self._pipeline(fake)
        state = TQASessionState(
            question="Total points have an inverse correlation to number of games lost.",
            df=df,
            table_schema=_build_table_schema(df),
            answer_mode="true_false",
            dataset_profile="tabfact",
        )

        result = pipeline.run(state)

        self.assertEqual(result.final_value, "true")
        self.assertEqual(result.final_answer, "true")
        self.assertNotIn("closed-label table classifier", "\n".join(fake.prompts))

    def test_tabfact_fuzzy_row_inclusion_tolerates_minor_entity_typo(self):
        df = pd.DataFrame(
            {
                "year": ["2005"],
                "award": ["tony award"],
                "category": ["best costume design"],
                "nominee": ["william ivey long"],
                "result": ["nominated"],
            }
        )

        value = TableQAPipeline._tabfact_fuzzy_row_inclusion_answer(
            "nominee for best costume design in 2005 at the tony award be qilliam ivey long",
            df,
        )

        self.assertEqual(value, "true")

    def test_tabfact_score_threshold_count_uses_team_side_of_score(self):
        df = pd.DataFrame(
            {
                "visitor": ["cleveland", "toronto", "cleveland"],
                "score": ["101 - 97", "120 - 97", "99 - 88"],
                "home": ["detroit", "cleveland", "indiana"],
            }
        )

        value = TableQAPipeline._tabfact_team_score_count_answer(
            "cleveland score 100 or more point in 1 game",
            df,
        )

        self.assertEqual(value, "true")

    def test_tabfact_score_threshold_count_without_team_name(self):
        df = pd.DataFrame(
            {
                "score": [
                    "8 - 6",
                    "3 - 2 (10)",
                    "13 - 4",
                    "12 - 3",
                    "6 - 4",
                ]
            }
        )

        value = TableQAPipeline._tabfact_score_threshold_count_answer(
            "2 game have a score of more than 10 point",
            df,
        )

        self.assertEqual(value, "true")

    def test_tabfact_same_metric_value_count_shortcut_counts_duplicate_values(self):
        df = pd.DataFrame(
            {
                "team": [
                    "auckland aces",
                    "northern districts",
                    "canterbury wizards",
                    "central districts stags",
                    "otago volts",
                ],
                "bonus points": ["2", "1", "1", "1", "1"],
            }
        )

        value = TableQAPipeline._tabfact_same_metric_value_count_answer(
            "4 team have the same amount of bonus point",
            df,
        )

        self.assertEqual(value, "true")

    def test_tabfact_match_type_count_shortcut_ignores_section_rows(self):
        df = pd.DataFrame(
            {
                "match": [
                    "sweden 1995 fifa women's world cup final",
                    "1",
                    "2",
                    "atlanta 1996 olympic women's football tournament",
                    "3",
                ],
                "competition": [
                    "sweden 1995 fifa women's world cup final",
                    "group match",
                    "gold medal match",
                    "atlanta 1996 olympic women's football tournament",
                    "semifinal",
                ],
            }
        )

        value = TableQAPipeline._tabfact_match_type_count_answer(
            "1 out of the 3 match be a gold medal match",
            df,
        )

        self.assertEqual(value, "true")

    def test_tabfact_final_record_shortcut_uses_last_valid_record(self):
        df = pd.DataFrame(
            {
                "week": ["week", "15", "16"],
                "record": ["record", "2 - 13", "2 - 14"],
            }
        )

        value = TableQAPipeline._tabfact_final_record_answer(
            "the 1985 tampa bay buccaneers season end their 1985 season with a 2 - 13 record",
            df,
        )

        self.assertEqual(value, "false")

    def test_tabfact_state_draft_only_player_shortcut_uses_state_abbreviation(self):
        df = pd.DataFrame(
            {
                "player": ["chris mills", "billy owens"],
                "hometown": ["los angeles , ca", "carlisle , pa"],
                "nba draft": [
                    "1st round - 22nd pick of 1993 draft ( cavs )",
                    "1st round - 3rd pick of 1991 draft ( kings )",
                ],
            }
        )

        value = TableQAPipeline._tabfact_state_draft_only_player_answer(
            "chris mill be the only player from california on the team and end up be a 1st round draft pick 1993",
            df,
        )

        self.assertEqual(value, "true")

    def test_tabfact_location_most_between_years_shortcut_counts_locations(self):
        df = pd.DataFrame(
            {
                "year location": [
                    "2009 wakayama",
                    "2008 yokohama",
                    "2007 chiba",
                    "2006 yokohama",
                    "2005 yokohama",
                    "2004 kobe",
                ]
            }
        )

        value = TableQAPipeline._tabfact_location_most_between_years_answer(
            "kobe host the most list of ittf pro tour winners in between 2004 and 2009",
            df,
        )

        self.assertEqual(value, "false")

    def test_tabfact_swept_date_series_shortcut_uses_record_progression(self):
        df = pd.DataFrame(
            {
                "date": ["june 14", "june 15", "june 16", "june 17"],
                "opponent": ["angels", "athletics", "athletics", "athletics"],
                "score": ["10 - 2", "6 - 0", "3 - 2", "10 - 9"],
                "record": ["18 - 46", "19 - 46", "20 - 46", "21 - 46"],
            }
        )

        value = TableQAPipeline._tabfact_swept_date_series_answer(
            "the blue jays swept the oakland athletics in the 3 game series from june 15 to 17th in 1979 toronto blue jays season",
            df,
        )

        self.assertEqual(value, "true")

    def test_tabfact_overtime_count_and_win_difference_shortcuts(self):
        overtime_df = pd.DataFrame({"score": ["2 - 2 ot", "1 - 0", "3 - 3 ot"]})
        race_df = pd.DataFrame({"winner": ["dick johnson", "john bowe", "dick johnson"]})

        self.assertEqual(
            TableQAPipeline._tabfact_overtime_count_answer(
                "the season go into overtime in 2 game",
                overtime_df,
            ),
            "true",
        )
        self.assertEqual(
            TableQAPipeline._tabfact_win_difference_answer(
                "dick johnson win 1 more race than john bowe in the championship",
                race_df,
            ),
            "true",
        )

    def test_tabfact_highest_location_and_same_city_shortcuts(self):
        school_df = pd.DataFrame(
            {
                "institution": ["A", "B"],
                "location": ["rio grande , ohio", "x , kentucky"],
                "enrollment": [3000, 2000],
            }
        )
        host_df = pd.DataFrame(
            {
                "host": ["villanova university", "saint joseph 's university"],
                "venue": ["the pavilion", "alumni memorial fieldhouse"],
                "city": ["villanova", "philadelphia"],
            }
        )

        self.assertEqual(
            TableQAPipeline._tabfact_highest_location_numeric_answer(
                "institution locate in ohio have the highest average enrollment",
                school_df,
            ),
            "true",
        )
        self.assertEqual(
            TableQAPipeline._tabfact_same_city_host_answer(
                "saint joseph 's university host at alumni memorial fieldhouse , locate in the same city as villanova university",
                host_df,
            ),
            "false",
        )

    def test_tabfact_second_stage_classification_winner_shortcut(self):
        df = pd.DataFrame(
            {
                "stage": [1, 2, 3, 4],
                "winner": ["a", "lucas sebastian haedo", "b", "lucas sebastian haedo"],
                "mountains classification": ["x", "x", "kenneth hanson", "kenneth hanson"],
            }
        )

        value = TableQAPipeline._tabfact_second_stage_classification_winner_answer(
            "kenneth hanson 's second stage as the mountain classification be when lucas sebastian haedo be the winner",
            df,
        )

        self.assertEqual(value, "true")

    def test_tabfact_numeric_count_and_highest_score_shortcuts(self):
        built = pd.DataFrame(
            {
                "model": ["300sel 6.3", "300sel 3.5", "300sel 4.5"],
                "number built": [6.526, 9.483, 2.533],
            }
        )
        diseases = pd.DataFrame(
            {
                "family": ["a", "b", "c", "d"],
                "replication site": ["nucleus", "cytoplasm", "nucleus", "nucleus"],
            }
        )
        games = pd.DataFrame(
            {
                "date": ["july 19", "july 20", "july 21"],
                "score": ["5 - 2", "10 - 9", "8 - 7"],
            }
        )

        self.assertEqual(
            TableQAPipeline._tabfact_numeric_difference_from_max_answer(
                "300sel 6.3 have 2.957 fewer number built than the model with the highest number built",
                built,
            ),
            "true",
        )
        self.assertEqual(
            TableQAPipeline._tabfact_column_value_count_answer(
                "3 of the viral disease replicate in the nucleus",
                diseases,
            ),
            "true",
        )
        self.assertEqual(
            TableQAPipeline._tabfact_highest_scoring_game_answer(
                "the highest scoring game be july 20 , 19 run be score",
                games,
            ),
            "true",
        )

    def test_tabfact_row_condition_and_unique_away_winner_shortcuts(self):
        doubles_df = pd.DataFrame(
            {
                "mens singles": ["jamie van hooijdonk", "irwansyah"],
                "womens doubles": ["kerry ann sheppard caroline harvey", "caroline harvey carissa turner"],
            }
        )
        fixture_df = pd.DataFrame(
            {
                "home team": ["footscray", "fitzroy", "essendon"],
                "home team score": ["12.9 (81)", "12.25 (97)", "15.17 (107)"],
                "away team": ["south melbourne", "richmond", "hawthorn"],
                "away team score": ["9.10 (64)", "16.9 (105)", "14.21 (105)"],
                "date": ["27 may 1972", "27 may 1972", "27 may 1972"],
            }
        )

        self.assertEqual(
            TableQAPipeline._tabfact_row_condition_count_answer(
                "there be 1 woman 's double team consist of kerry ann sheppard and carolina harvey when jamie van hooijdonk compete in the men 's single",
                doubles_df,
            ),
            "true",
        )
        self.assertEqual(
            TableQAPipeline._tabfact_unique_side_winner_answer(
                "on may 27 only 1 away team , richmond , win their game",
                fixture_df,
            ),
            "true",
        )

    def test_tabfact_episode_order_shortcut_uses_series_order(self):
        df = pd.DataFrame(
            {
                "no for series": [7, 10],
                "title": ["the witchfinder", "sweet dreams"],
                "original air date": ["7 november 2009", "28 november 2009"],
            }
        )

        self.assertEqual(
            TableQAPipeline._tabfact_episode_order_answer(
                "the sweet dream episode happen earlier in the series than the witchfinder",
                df,
            ),
            "false",
        )
        self.assertEqual(
            TableQAPipeline._tabfact_episode_order_answer(
                "the sweet dream episode happen later in the series than the witchfinder",
                df,
            ),
            "true",
        )

    def test_tabfact_episode_credit_count_shortcut_counts_matching_people(self):
        df = pd.DataFrame(
            {
                "title": ["a", "b", "c", "d"],
                "directed by": ["david moore", "jeremy webb", "david moore", "david moore"],
                "written by": ["x", "lucy watkins", "y", "z"],
            }
        )

        self.assertEqual(
            TableQAPipeline._tabfact_episode_credit_count_answer(
                "david moore direct 3 episode of series 2",
                df,
            ),
            "true",
        )
        self.assertEqual(
            TableQAPipeline._tabfact_episode_credit_count_answer(
                "lucy watkins only write 1 episode of series 2",
                df,
            ),
            "true",
        )

    def test_tabfact_goal_competition_count_shortcut_counts_goal_rows(self):
        df = pd.DataFrame(
            {
                "goal": [1, 2, 3, 4],
                "competition": ["friendly", "2006 fifa world cup", "friendly", "friendly"],
            }
        )

        self.assertEqual(
            TableQAPipeline._tabfact_goal_competition_count_answer(
                "rafael marquez score 3 goal in his career at international friendly competition",
                df,
            ),
            "true",
        )

    def test_tabfact_not_fewer_than_any_other_shortcut_compares_all_peers(self):
        df = pd.DataFrame(
            {
                "player": ["vaea anitoni", "paul emerick", "todd clever"],
                "tries": [26, 17, 11],
            }
        )

        self.assertEqual(
            TableQAPipeline._tabfact_not_fewer_than_any_other_answer(
                "paul emerick do not have fewer tries than any other player",
                df,
            ),
            "false",
        )

    def test_tabfact_nonzero_metric_count_shortcut_checks_entity_and_count(self):
        df = pd.DataFrame(
            {
                "player": ["chris wyles", "mike hercus", "david fee"],
                "drop": [1, 4, 0],
            }
        )

        self.assertEqual(
            TableQAPipeline._tabfact_nonzero_metric_count_answer(
                "chris wyles be 1 of the 2 player with drop during their time on the rugby team",
                df,
            ),
            "true",
        )
        self.assertEqual(
            TableQAPipeline._tabfact_nonzero_metric_count_answer(
                "chris wyles be the only player with drop during his time on the rugby team",
                df,
            ),
            "false",
        )

    def test_tabfact_max_metric_span_shortcut_uses_inclusive_year_span(self):
        df = pd.DataFrame(
            {
                "player": ["chris wyles", "mike hercus"],
                "span": ["2007 -", "2002 - 2009"],
                "drop": [1, 4],
            }
        )

        self.assertEqual(
            TableQAPipeline._tabfact_max_metric_span_answer(
                "the greatest number of drop from 1 player happen over the span of 8 year",
                df,
            ),
            "true",
        )

    def test_tabfact_goal_result_shortcut_checks_scoreless_and_loss_counts(self):
        df = pd.DataFrame(
            {
                "date": ["24 june 2006", "11 june 2010", "30 october 2013"],
                "score": ["1 - 0", "1 - 1", "1 - 0"],
                "result": ["1 - 2 ( aet )", "1 - 1", "4 - 2"],
                "competition": ["2006 fifa world cup", "2010 fifa world cup", "friendly"],
            }
        )

        self.assertEqual(
            TableQAPipeline._tabfact_goal_result_answer(
                "rafael marquez score a goal at the 2006 , but remain scoreless during the 2010 fifa world cup",
                df,
            ),
            "false",
        )
        self.assertEqual(
            TableQAPipeline._tabfact_goal_result_answer(
                "mexico only lose 1 time in international competition when rafael marquez score a goal",
                df,
            ),
            "true",
        )

    def test_tabfact_last_row_entity_shortcut_checks_table_order(self):
        df = pd.DataFrame(
            {
                "team 1": ["siauliai", "triumph", "ask riga"],
                "agg": ["136 - 167", "146 - 159", "142 - 137"],
            }
        )

        self.assertEqual(
            TableQAPipeline._tabfact_last_row_entity_answer(
                "ask riga be the last place in the competition in the basketball",
                df,
            ),
            "true",
        )
        self.assertEqual(
            TableQAPipeline._tabfact_last_row_entity_answer(
                "triumph be the last place in the competition in the basketball",
                df,
            ),
            "false",
        )

    def test_tabfact_condition_value_shortcut_matches_row_conditions(self):
        df = pd.DataFrame(
            {
                "position in table": ["13th", "3rd", "10th"],
                "manner of departure": ["resigned", "resigned", "resigned"],
                "date of vacancy": ["20 february 2011", "26 february 2011", "28 february 2011"],
            }
        )

        self.assertEqual(
            TableQAPipeline._tabfact_condition_value_answer(
                "the date of vacancy when the position in the table be 10th and the manner of departure be resign be 28 february 2011",
                df,
            ),
            "true",
        )

    def test_tabfact_threshold_implication_shortcut_checks_matching_rows(self):
        df = pd.DataFrame(
            {
                "employees (average / year)": [26538, 26554, 32363],
                "net profit / loss (sek)": [1234000000, 4936000000, 418000000],
                "basic eps (sek)": [3.87, 28.10, 1.06],
            }
        )

        self.assertEqual(
            TableQAPipeline._tabfact_threshold_implication_answer(
                "when the net profit / loss (sek) be larger than 4935000000 , and a basic eps (sek) larger than 1.06 the number of employee (average / year) be larger than 4",
                df,
            ),
            "true",
        )

    def test_tabfact_same_side_score_comparison_uses_away_score(self):
        df = pd.DataFrame(
            {
                "home team": ["melbourne", "carlton"],
                "home team score": ["11.14 (80)", "8.11 (59)"],
                "away team": ["north melbourne", "geelong"],
                "away team score": ["10.12 (72)", "9.11 (65)"],
            }
        )

        self.assertEqual(
            TableQAPipeline._tabfact_same_side_score_comparison_answer(
                "geelong get a higher score than north melbourne as an away team",
                df,
            ),
            "false",
        )

    def test_tabfact_highest_shutout_score_shortcut_checks_entity_and_score(self):
        df = pd.DataFrame(
            {
                "home team": ["burnley", "woking", "chester city"],
                "score": ["2 - 0", "5 - 1", "4 - 0"],
                "away team": ["stoke city", "merthyr tydfil", "leek town"],
            }
        )

        self.assertEqual(
            TableQAPipeline._tabfact_highest_shutout_score_answer(
                "chester city play the highest scoring shut out game : 4 to 0",
                df,
            ),
            "true",
        )

    def test_tabfact_entity_metric_comparison_shortcut_selects_loss_column(self):
        df = pd.DataFrame(
            {
                "club": ["london broncos", "warrington wolves"],
                "lost": [5, 6],
                "points": [2, 0],
            }
        )

        self.assertEqual(
            TableQAPipeline._tabfact_entity_metric_comparison_answer(
                "london bronco club have a lower number of loss than the warrington wolves club",
                df,
            ),
            "true",
        )

    def test_tabfact_entity_extreme_metric_shortcut_parses_uncertainty_values(self):
        df = pd.DataFrame(
            {
                "name": ["ngc 1533", "ngc 1705", "ngc 1596"],
                "redshift (km / s )": ["790 +/- 5", "633 +/- 6", "1510 +/- 8"],
            }
        )

        self.assertEqual(
            TableQAPipeline._tabfact_entity_extreme_metric_answer(
                "ngc 1705 have the smallest redshift at 633 kilometer per second plus or minus 6 kilometer per second",
                df,
            ),
            "true",
        )

    def test_tabfact_fuzzy_row_inclusion_ignores_relation_words(self):
        df = pd.DataFrame(
            {
                "title": ["a fistful of secrets"],
                "directed by": ["robert j. metoyer"],
                "production code": ["2398204"],
            }
        )

        self.assertEqual(
            TableQAPipeline._tabfact_fuzzy_row_inclusion_answer(
                "robert j metoyer direct a fist full of secret (production code 2398204)",
                df,
            ),
            "true",
        )

    def test_tabfact_least_threshold_shortcut_checks_global_minimum(self):
        df = pd.DataFrame(
            {
                "opponent": ["white sox", "white sox", "royals"],
                "attendance": [40299, 746, 12533],
            }
        )

        self.assertEqual(
            TableQAPipeline._tabfact_least_threshold_answer(
                "less than 1000 crowd attend the game against the white sox make it the least attended game",
                df,
            ),
            "true",
        )

    def test_tabfact_extreme_metric_belongs_shortcut_checks_owner_and_value(self):
        built_df = pd.DataFrame(
            {
                "class": ["i4", "j1"],
                "no built": [5, 1],
            }
        )
        enrollment_df = pd.DataFrame(
            {
                "institution": ["oklahoma baptist university", "texas college"],
                "enrollment": [1871, 600],
            }
        )
        golf_df = pd.DataFrame(
            {
                "player": ["steve stricker", "colin montgomerie"],
                "country": ["united states", "scotland"],
                "score": ["70 + 69 = 139", "69 + 71 = 140"],
            }
        )

        self.assertEqual(
            TableQAPipeline._tabfact_extreme_metric_belongs_answer(
                "the lowest no built be class i4",
                built_df,
            ),
            "false",
        )
        self.assertEqual(
            TableQAPipeline._tabfact_extreme_metric_belongs_answer(
                "the smallest enrollment belongs to oklahoma baptist university university at 1871",
                enrollment_df,
            ),
            "false",
        )
        self.assertEqual(
            TableQAPipeline._tabfact_extreme_metric_belongs_answer(
                "steve stricker of united state have the lowest score among all the player",
                golf_df,
            ),
            "true",
        )

    def test_tabfact_threshold_count_shortcut_counts_numeric_score_column(self):
        df = pd.DataFrame(
            {
                "away team": ["north melbourne", "st kilda", "richmond", "collingwood"],
                "away team score": ["6.12 (48)", "10.11 (71)", "9.15 (69)", "10.14 (74)"],
            }
        )

        self.assertEqual(
            TableQAPipeline._tabfact_threshold_count_answer(
                "a total of 2 away team have an away team score higher than 10.00",
                df,
            ),
            "true",
        )

    def test_tabfact_first_n_rows_count_shortcut_uses_table_order(self):
        df = pd.DataFrame(
            {
                "pick": [11, 47, 64, 80],
                "college / junior / club team": [
                    "london knights ( oha )",
                    "cornwall royals ( oha )",
                    "london knights ( oha )",
                    "regina pats ( wchl )",
                ],
            }
        )

        self.assertEqual(
            TableQAPipeline._tabfact_first_n_rows_count_answer(
                "2 of the first 3 draft pick come from the london knight",
                df,
            ),
            "true",
        )

    def test_tabfact_entity_metric_threshold_shortcut_checks_episode_number(self):
        df = pd.DataFrame(
            {
                "no in series": [147, 148, 159],
                "title": ["wrong - way tanner", "tough love", "the test"],
            }
        )

        self.assertEqual(
            TableQAPipeline._tabfact_entity_metric_threshold_answer(
                "the episode number in the series the test be before 148.0",
                df,
            ),
            "false",
        )

    def test_tabfact_year_column_value_shortcut_matches_na_values(self):
        df = pd.DataFrame(
            {
                "year": [2010, 2011],
                "reader 's vote": ["peter doyle", "na"],
            }
        )

        self.assertEqual(
            TableQAPipeline._tabfact_year_column_value_answer(
                "in 2011 , n / a be the reader 's vote",
                df,
            ),
            "true",
        )

    def test_tabfact_game_result_score_shortcut_checks_opponent_win(self):
        df = pd.DataFrame(
            {
                "game": [64, 65],
                "team": ["miami", "orlando"],
                "score": ["w 83 - 74 (ot)", "l 79 - 92 (ot)"],
            }
        )

        self.assertEqual(
            TableQAPipeline._tabfact_game_result_score_answer(
                "orlando win game 65 with a score of 79 - 92",
                df,
            ),
            "true",
        )

    def test_tabfact_date_metric_difference_shortcut_compares_week_later(self):
        df = pd.DataFrame(
            {
                "date": ["september 18 , 1988", "september 25 , 1988"],
                "attendance": [63990, 56012],
            }
        )

        self.assertEqual(
            TableQAPipeline._tabfact_date_metric_difference_answer(
                "attendance on september 18 , 1988 be 7978 more than the game a week later",
                df,
            ),
            "true",
        )

    def test_tabfact_condition_metric_value_shortcut_matches_same_row(self):
        df = pd.DataFrame(
            {
                "year": ["1999", "totals"],
                "assists": ["4", "7"],
                "total points": ["18", "39"],
            }
        )

        self.assertEqual(
            TableQAPipeline._tabfact_condition_metric_value_answer(
                "the total number of point in the year with 7 assist be 39",
                df,
            ),
            "true",
        )

    def test_tabfact_score_sum_comparison_shortcut_uses_total_score(self):
        df = pd.DataFrame(
            {
                "player": ["harvie ward", "jack fleck"],
                "score": ["74 + 70 = 144", "76 + 69 = 145"],
            }
        )

        self.assertEqual(
            TableQAPipeline._tabfact_entity_score_sum_comparison_answer(
                "jack fleck score less point that harvie ward in the 1955 u.s. open (golf) us open",
                df,
            ),
            "false",
        )

    def test_tabfact_country_pair_shortcut_checks_each_entity(self):
        df = pd.DataFrame(
            {
                "player": ["justin leonard", "jesper parnevik", "angel cabrera"],
                "country": ["united states", "sweden", "argentina"],
            }
        )

        self.assertEqual(
            TableQAPipeline._tabfact_country_pair_answer(
                "jesper parnevik be from sweden , while justin leonard be from argentina",
                df,
            ),
            "false",
        )

    def test_tabfact_zero_gold_count_shortcut_counts_zero_medal_rows(self):
        df = pd.DataFrame(
            {
                "nation": ["brazil", "argentina", "peru", "aruba"],
                "gold": ["6", "0", "0", "0"],
            }
        )

        self.assertEqual(
            TableQAPipeline._tabfact_zero_gold_count_answer(
                "there be 3 nation that didn't have any gold medal",
                df,
            ),
            "true",
        )

    def test_tabfact_every_game_before_date_shortcut_excludes_boundary_date(self):
        df = pd.DataFrame(
            {
                "date": ["sept 2", "sept 23", "sept 30"],
                "result": ["win", "win", "loss"],
            }
        )

        self.assertEqual(
            TableQAPipeline._tabfact_every_before_date_result_answer(
                "every game before september 30 be a victory for the dolphin",
                df,
            ),
            "true",
        )

    def test_tabfact_replay_count_month_shortcut_counts_replay_rows(self):
        df = pd.DataFrame(
            {
                "tie no": ["1", "replay", "7", "replay", "13", "replay"],
                "date": [
                    "24 january 1976",
                    "27 january 1976",
                    "24 january 1976",
                    "28 january 1976",
                    "24 january 1976",
                    "27 january 1976",
                ],
            }
        )

        self.assertEqual(
            TableQAPipeline._tabfact_replay_count_month_answer(
                "3 match be replay in january 1976",
                df,
            ),
            "true",
        )

    def test_tabfact_lowest_attendance_weeks_shortcut_checks_site_subset(self):
        df = pd.DataFrame(
            {
                "week": ["1", "2", "10", "12", "14"],
                "game site": ["mile high stadium"] * 5,
                "attendance": [73564, 73899, 73996, 73984, 74192],
            }
        )

        self.assertEqual(
            TableQAPipeline._tabfact_lowest_attendance_weeks_answer(
                "during the 1982 denver broncos season , week 1 , 2 and 10 be play with the lowest attendance at the mile high stadium",
                df,
            ),
            "false",
        )

    def test_tabfact_replay_home_team_win_shortcut_checks_draw_replays(self):
        df = pd.DataFrame(
            {
                "tie no": ["1", "replay", "16", "replay"],
                "home team": ["nelson", "york city", "exeter city", "coventry city"],
                "score": ["1 - 1", "3 - 2", "1 - 1", "1 - 2"],
                "away team": ["york city", "nelson", "coventry city", "exeter city"],
            }
        )

        self.assertEqual(
            TableQAPipeline._tabfact_replay_home_team_win_answer(
                "both of the game that have to be replay , due to the first match tying , be ultimately win by the home team",
                df,
            ),
            "false",
        )

    def test_tabfact_entity_tenure_contains_other_tenure_shortcut(self):
        df = pd.DataFrame(
            {
                "player": ["adrian dantley", "brad davis"],
                "years for jazz": ["1979 - 86", "1979 - 80"],
            }
        )

        self.assertEqual(
            TableQAPipeline._tabfact_entity_tenure_contains_answer(
                "adrian dantley be on the team the entire time that brad davis be",
                df,
            ),
            "true",
        )

    def test_tabfact_aircraft_call_sign_shortcut_requires_same_strict_pilot_row(self):
        df = pd.DataFrame(
            {
                "pilot": ["capt richard s ritchie", "capt rs ritchie"],
                "aircraft": ["f - 4d 66 - 7463", "f - 4e 67 - 0362"],
                "call sign": ["oyster 03", "paula 01"],
            }
        )

        self.assertEqual(
            TableQAPipeline._tabfact_aircraft_call_sign_answer(
                "capt richard s ritchie fly a f - 4e 67 - 0362 and have the call sign paula 01",
                df,
            ),
            "false",
        )

    def test_tabfact_venue_competition_date_shortcut_requires_same_row(self):
        df = pd.DataFrame(
            {
                "date": ["march 7 , 2007", "june 2 , 2007"],
                "venue": ["shymkent , kazakhstan", "baku , azerbaijan"],
                "competition": ["friendly", "uefa euro 2008 qualifying"],
            }
        )

        self.assertEqual(
            TableQAPipeline._tabfact_venue_competition_date_answer(
                "the friendly competition at the venue shymkent , kazakhstan , be on june 2 , 2007",
                df,
            ),
            "false",
        )

    def test_tabfact_grand_final_score_loss_shortcut_checks_losing_side(self):
        df = pd.DataFrame(
            {
                "score": ["42 - 14"],
                "premiers": ["south sydney rabbitohs"],
                "runners up": ["manly - warringah sea eagles"],
                "details": ["1951 nswrfl grand final"],
            }
        )

        self.assertEqual(
            TableQAPipeline._tabfact_score_but_lose_answer(
                "south sydney rabbitohs score 42 point but lose the game during the 1951 nswrfl grand final",
                df,
            ),
            "false",
        )

    def test_tabfact_second_smallest_metric_shortcut_checks_ranked_value(self):
        df = pd.DataFrame(
            {
                "month & year": ["february 2007", "november 2007", "march 2013"],
                "title": ["us special", "botswana special", "africa special"],
                "budget": ["1000", "1500", "1500"],
            }
        )

        self.assertEqual(
            TableQAPipeline._tabfact_second_smallest_metric_answer(
                "the second smallest budget be 1000 for the us special show february 2007",
                df,
            ),
            "false",
        )

    def test_tabfact_retirement_threshold_shortcut_counts_non_finish_statuses(self):
        df = pd.DataFrame(
            {
                "driver": [f"driver {idx}" for idx in range(20)],
                "time / retired": [
                    "1:35:13.284",
                    "+ 23.911",
                    "+ 1 lap",
                    "ignition",
                    "overheating",
                    "fuel system",
                    "gearbox",
                    "engine",
                    "fuel system",
                    "turbo",
                    "turbo",
                    "engine",
                    "turbo",
                    "overheating",
                    "collision",
                    "collision",
                    "collision",
                    "collision",
                    "collision",
                    "collision",
                ],
            }
        )

        self.assertEqual(
            TableQAPipeline._tabfact_retirement_threshold_answer(
                "there be less than 17 player who untimely retire during the 1984 european grand prix",
                df,
            ),
            "false",
        )

    def test_tabfact_entity_attribute_shortcut_checks_subject_row(self):
        df = pd.DataFrame(
            {
                "company": ["epcor", "high speed alliance"],
                "type": ["subsidiary", "joint venture"],
            }
        )

        self.assertEqual(
            TableQAPipeline._tabfact_entity_attribute_answer(
                "the company of epcor have a joint control entity type",
                df,
            ),
            "false",
        )
        self.assertIsNone(
            TableQAPipeline._tabfact_entity_attribute_answer(
                "italy have 0 gold medal and more than 1 silver",
                pd.DataFrame({"nation": ["italy"], "gold": [0], "silver": [2]}),
            )
        )

    def test_tabfact_same_row_cell_mention_shortcut_requires_joint_row(self):
        enzymes = pd.DataFrame(
            {
                "enzyme": ["ala synthase", "uroporphyrinogen iii decarboxylase"],
                "location": ["mitochondrion", "cytosol"],
                "porphyria": ["none", "porphyria cutanea tarda"],
            }
        )
        games = pd.DataFrame(
            {
                "date": ["may 11", "may 13"],
                "home": ["vancouver", "los angeles"],
                "score": ["4 - 3", "3 - 5"],
                "decision": ["mclean", "mclean"],
                "series": ["2 - 3", "2 - 4"],
            }
        )

        self.assertEqual(
            TableQAPipeline._tabfact_same_row_cell_mention_answer(
                "enzyme ala synthase have a location mitochondrion and porphyria of porphyria cutanea tarda",
                enzymes,
            ),
            "false",
        )
        self.assertEqual(
            TableQAPipeline._tabfact_same_row_cell_mention_answer(
                "when the decision be mclean with the series at 2 - 3 and the home team be vancouver be the score be 3 - 5 with a date of may 13",
                games,
            ),
            "false",
        )
        self.assertIsNone(
            TableQAPipeline._tabfact_same_row_cell_mention_answer(
                "the diameter of the coin with an equivalence of 0.60 be 24.5 mm with a value of less than 100",
                pd.DataFrame(
                    {
                        "diameter": ["24.5 mm"],
                        "equivalence": ["0.60"],
                        "value": ["100"],
                    }
                ),
            )
        )

    def test_tabfact_column_value_count_shortcut_handles_result_and_year_columns(self):
        elections = pd.DataFrame(
            {
                "district": [f"virginia {idx}" for idx in range(13)],
                "result": ["re - elected"] * 11 + ["lost re - election", "retired"],
            }
        )
        tennis = pd.DataFrame(
            {
                "tournament": ["australian open", "french open", "wimbledon"],
                "2010": ["sf", "1r", "1r"],
            }
        )

        self.assertEqual(
            TableQAPipeline._tabfact_column_value_count_assertion_answer(
                "11 representative be re - elect in their district in united states house of representatives elections , 1794",
                elections,
            ),
            "true",
        )
        self.assertEqual(
            TableQAPipeline._tabfact_column_value_count_assertion_answer(
                "there be only 1 sf in 2010 for michael kohlmann",
                tennis,
            ),
            "true",
        )

    def test_tabfact_two_entity_appearance_count_shortcut_counts_home_away_columns(self):
        df = pd.DataFrame(
            {
                "home team": ["arsenal", "liverpool"],
                "away team": ["chelsea", "portsmouth"],
            }
        )

        self.assertEqual(
            TableQAPipeline._tabfact_two_entity_appearance_count_answer(
                "both the arsenal and chelsea team be only feature on the list a single time",
                df,
            ),
            "true",
        )

    def test_tabfact_first_last_time_gap_shortcut_uses_relative_gap_times(self):
        df = pd.DataFrame(
            {
                "team": ["switzerland", "france", "mexico", "china"],
                "driver": ["neel jani", "loic duval", "michel jourdain jr", "cong fu cheng"],
                "time": ["18'20.910", "+ 3.792", "+ 47.416", "mechanical"],
            }
        )

        self.assertEqual(
            TableQAPipeline._tabfact_first_last_time_gap_answer(
                "there be 47.416 second between the first and last race car driver in the 2007 - 08 a1 grand prix of nations , malaysia in malaysia",
                df,
            ),
            "true",
        )

    def test_tabfact_entity_metric_difference_shortcut_handles_decimal_comma_lengths(self):
        df = pd.DataFrame(
            {
                "ship name": ["aqua maria", "aqua spirit"],
                "length": ["101 , 3 m", "75 m"],
            }
        )

        self.assertEqual(
            TableQAPipeline._tabfact_entity_metric_difference_value_answer(
                "the aqua spirit be 26.3 meter smaller in length than the aqua maria",
                df,
            ),
            "true",
        )

    def test_crt_consecutive_year_medalist_shortcut_checks_all_medal_columns(self):
        df = pd.DataFrame(
            {
                "year": [1990, 1991, 1993],
                "gold": ["satu pusila ( fin )", "satu pusila ( fin )", "other"],
                "silver": ["alpha", "beta", "gamma"],
                "bronze": ["delta", "epsilon", "zeta"],
            }
        )

        value = TableQAPipeline._crt_consecutive_year_medalist_answer(
            "Have any athletes won medals in the Double Trap competition in consecutive years?",
            df,
        )

        self.assertEqual(value, "Yes")

    def test_crt_scalar_shortcuts_cover_counts_ratios_and_modes(self):
        electors = pd.DataFrame(
            {"cardinalatial title": ["priest of a", "deacon of b", "priest of c"]}
        )
        draft = pd.DataFrame({"nationality": ["canada", "canada", "usa", "russia"]})
        sources = pd.DataFrame({"order and title": ["cardinal - deacon", "cardinal - priest", "cardinal - deacon"]})

        self.assertEqual(
            TableQAPipeline._crt_count_role_answer("How many of the electors were priests?", electors),
            2,
        )
        self.assertEqual(
            TableQAPipeline._crt_proportion_nationality_answer(
                "What is the proportion of Canadian to non-Canadian players drafted?",
                draft,
            ),
            "1:1",
        )
        self.assertEqual(
            TableQAPipeline._crt_common_cardinal_source_answer(
                "What is the most common source of elevation to cardinalhood (bishop, priest, deacon)?",
                sources,
            ),
            "deacon",
        )

    def test_crt_first_architecture_leftover_and_viewership_shortcuts(self):
        windows = pd.DataFrame(
            {
                "name": ["windows nt", "windows 2000", "windows xp"],
                "release date": ["1999 - 01 - 01", "2000 - 02 - 17", "2001 - 10 - 25"],
                "supported architectures": ["ia - 32", "ia - 32 , ia - 64", "ia - 32 , x86 - 64"],
            }
        )
        funds = pd.DataFrame(
            {
                "candidate": ["a", "barack obama", "combined total"],
                "all receipts": [10, 100, 110],
                "all disbursements": [8, 40, 48],
            }
        )
        episodes = pd.DataFrame(
            {"title": ["Big Time Gold", "Other"], "us viewers": [10.0, 5.0]}
        )

        self.assertEqual(
            TableQAPipeline._crt_first_supported_architecture_answer(
                "Which version of Windows was the first to support 64-bit architecture?",
                windows,
            ),
            "windows 2000",
        )
        self.assertEqual(
            TableQAPipeline._crt_largest_money_leftover_answer(
                "Who had the largest amount of money left over after all disbursements were made?",
                funds,
            ),
            "barack obama",
        )
        self.assertEqual(
            TableQAPipeline._crt_episode_viewership_vs_season_average_answer(
                'How does the viewership of the "Big Time Gold" episode compare to the average viewership of the season it was aired in?',
                episodes,
            ),
            "better",
        )

    def test_crt_duplicate_victory_type_and_diverse_content_shortcuts(self):
        leaders = pd.DataFrame({"player": ["a", "b", "a"]})
        fights = pd.DataFrame(
            {
                "res": ["win", "win", "win", "loss"],
                "method": ["decision (split)", "decision (unanimous)", "tko", "submission"],
            }
        )
        tv = pd.DataFrame({"content": ["calcio", "calcio , ppv wrestling"]})

        self.assertEqual(
            TableQAPipeline._crt_duplicate_named_entity_answer(
                "Are there any players who have led in more than one Grand Slam tournament?",
                leaders,
            ),
            "Yes",
        )
        self.assertEqual(
            TableQAPipeline._crt_victory_type_stands_out_answer(
                "Does Rob Emerson have any specific type of victory that stands out more than others?",
                fights,
            ),
            "Yes",
        )
        self.assertEqual(
            TableQAPipeline._crt_diverse_content_beyond_answer(
                "Are there any specific television services that stand out in terms of offering diverse content beyond football (calcio)?",
                tv,
            ),
            "Yes",
        )

    def test_crt_medal_probability_ratio_and_owner_shortcuts(self):
        medals = pd.DataFrame(
            {
                "rank": [1, 2, 3],
                "nation": ["netherlands", "france", "spain"],
                "gold": [1, 2, 0],
                "silver": [2, 0, 3],
                "bronze": [0, 1, 1],
                "total": [3, 3, 4],
            }
        )
        stations = pd.DataFrame(
            {
                "call sign": ["a", "b", "c", "d", "e"],
                "owner": ["harvard broadcasting", "other", "harvard broadcasting", "other", "other"],
            }
        )
        sports = pd.DataFrame(
            {
                "nation": ["a", "b", "a"],
                "sport": ["skiing", "skiing", "skating"],
                "gold": [2, 0, 1],
                "silver": [1, 1, 0],
                "bronze": [1, 1, 0],
                "total": [4, 2, 1],
            }
        )

        self.assertEqual(
            TableQAPipeline._crt_owned_percentage_answer(
                "What percentage of radio stations in Melville are owned by Harvard Broadcasting",
                stations,
            ),
            0.4,
        )
        self.assertEqual(
            TableQAPipeline._crt_medal_probability_answer(
                "What is the probability that a randomly chosen medalist in the championships is from the Netherlands?",
                medals,
            ),
            "30.0%",
        )
        self.assertEqual(
            TableQAPipeline._crt_medal_ratio_answer(
                "What is the ratio of silver to gold medals among the nations that earned a gold medal?",
                medals,
            ),
            "2:3",
        )
        self.assertEqual(
            TableQAPipeline._crt_medal_ratio_answer(
                "What is the ratio of gold medals to total medals won by the top three countries combined?",
                medals,
            ),
            0.3,
        )
        self.assertEqual(
            TableQAPipeline._crt_majority_medal_by_group_answer(
                "Are there any sports in which a single country won the majority of medals?",
                sports,
            ),
            "Yes",
        )

    def test_crt_recognition_category_shortcut_detects_repeated_award_theme(self):
        df = pd.DataFrame(
            {
                "award": [
                    "milf / cougar performer of the year",
                    "best cougar / milf performer",
                    "web star of the year",
                ],
                "result": ["nominated", "won", "nominated"],
            }
        )

        value = TableQAPipeline._crt_recognition_category_advantage_answer(
            "Are there any award categories for which Brandi Love has a greater chance of being recognized compared to other categories?",
            df,
        )

        self.assertEqual(value, "Yes")

    def test_crt_century_manufacturing_shortcut_counts_year_ranges(self):
        df = pd.DataFrame(
            {
                "locomotive": ["A", "B", "C", "D"],
                "manufactured": ["1888-1899", "1899-1901", "1902", "1904"],
            }
        )
        planner_output = (
            "[PLAN]\nStep1: Count only start years.\n[CODE]\n"
            "final_answer_value = 'less'\n"
        )
        fake = FakePipelineLLM(
            semantic_score=0.8,
            rows=[],
            cols=["manufactured"],
            planner_outputs=[planner_output],
        )
        pipeline, _ = self._pipeline(fake)
        state = TQASessionState(
            question=(
                "How does the quantity of locomotives manufactured in the 1900s "
                "compare to those manufactured in the 1800s? Answer with only "
                "'more', 'less' or 'equal' and nothing else."
            ),
            df=df,
            table_schema=_build_table_schema(df),
            answer_mode="more_less_equal",
            dataset_profile="crt",
        )

        result = pipeline.run(state)

        self.assertEqual(result.final_value, "more")
        self.assertEqual(result.final_answer, "more")
        self.assertNotIn("table reasoning planner", "\n".join(fake.prompts))

    def test_crt_consistent_top_k_shortcut_intersects_years(self):
        df = pd.DataFrame(
            {
                "location": ["Park A", "Park B", "Park C"],
                "rank": [1, 2, 3],
                "2008": [100, 90, 80],
                "2009": [101, 91, 81],
                "2010": [102, 92, 82],
                "2011": [103, 93, 83],
                "2012": [104, 94, 84],
            }
        )
        fake = FakePipelineLLM(
            semantic_score=0.05,
            rows=["Park A"],
            cols=["rank 2008", "rank 2012"],
            classification_output='{"label":"No"}',
        )
        pipeline, _ = self._pipeline(fake)
        state = TQASessionState(
            question=(
                "Are there any locations that have consistently ranked in the top "
                "10 amusement parks in the United States from 2008 to 2012?"
            ),
            df=df,
            table_schema=_build_table_schema(df),
            answer_mode="yes_no",
            dataset_profile="crt",
        )

        result = pipeline.run(state)

        self.assertEqual(result.final_value, "Yes")
        self.assertEqual(result.final_answer, "Yes")
        self.assertNotIn("closed-label table classifier", "\n".join(fake.prompts))

    def test_crt_hemisphere_answer_is_completed_to_full_label(self):
        df = pd.DataFrame(
            {
                "event": ["a", "b", "c"],
                "country": ["china", "japan", "united states"],
            }
        )
        planner_output = (
            "[PLAN]\nStep1: Count hemispheres.\n[CODE]\n"
            "final_answer_value = 'Eastern'\n"
        )
        fake = FakePipelineLLM(
            semantic_score=0.8,
            rows=[],
            cols=["country"],
            planner_outputs=[planner_output],
        )
        pipeline, _ = self._pipeline(fake)
        state = TQASessionState(
            question="Are there more events held in the eastern or western hemisphere?",
            df=df,
            table_schema=_build_table_schema(df),
            dataset_profile="crt",
        )

        result = pipeline.run(state)

        self.assertEqual(result.final_value, "eastern hemisphere")
        self.assertEqual(result.final_answer, "eastern hemisphere")

    def test_classification_rejects_output_without_allowed_label(self):
        df = pd.DataFrame({"Event": ["A"], "Acts": [5]})
        fake = FakePipelineLLM(
            semantic_score=0.8,
            rows=["A"],
            cols=["Event", "Acts"],
            classification_output="uncertain",
        )
        pipeline, _ = self._pipeline(fake)
        state = TQASessionState(
            question="Is this supported?",
            df=df,
            table_schema=_build_table_schema(df),
            answer_mode="yes_no",
        )

        with self.assertRaisesRegex(RuntimeError, "allowed label"):
            pipeline.run(state)

    def test_selective_mode_records_risk_and_budget(self):
        df = pd.DataFrame({"Name": ["Alpha"], "Value": [7]})
        fake = FakePipelineLLM(semantic_score=0.05, rows=["Alpha"], cols=["Value"])
        tracker = LLMCallTracker(fake)
        pipeline = TableQAPipeline(
            router=RouterAgent(tracker),
            planner=PlannerAgent(tracker),
            calculator=Calculator(),
            critic=CriticAgent(tracker),
            final_answer_agent=FinalAnswerAgent(tracker),
            enable_selective_collaboration=True,
            mact_avg_tokens=8867.0,
        )
        state = TQASessionState(
            question="What is the value for Alpha?",
            df=df,
            table_schema=_build_table_schema(df),
            dataset_profile="wtq",
        )

        result = pipeline.run(state)

        self.assertIsNotNone(result.risk_assessment)
        self.assertIn(result.risk_level, {"light", "medium", "high", "fallback"})
        self.assertIsNotNone(result.evidence_pack)
        self.assertIn("avg_tokens", result.budget_state)

    def test_disable_risk_scoring_holds_medium_risk_for_ablation(self):
        df = pd.DataFrame({"Name": ["Alpha"], "Value": [7]})
        fake = FakePipelineLLM(semantic_score=0.05, rows=["Alpha"], cols=["Value"])
        tracker = LLMCallTracker(fake)
        pipeline = TableQAPipeline(
            router=RouterAgent(tracker),
            planner=PlannerAgent(tracker),
            calculator=Calculator(),
            critic=CriticAgent(tracker),
            final_answer_agent=FinalAnswerAgent(tracker),
            enable_selective_collaboration=True,
            disable_risk_scoring=True,
        )
        state = TQASessionState(
            question="What is the value for Alpha?",
            df=df,
            table_schema=_build_table_schema(df),
            dataset_profile="wtq",
        )

        result = pipeline.run(state)

        self.assertEqual(result.risk_assessment.level, "medium")
        self.assertEqual(
            result.risk_assessment.feature_evidence.get("ablation"),
            "risk_scoring_disabled",
        )
        self.assertFalse(result.cost_metrics["risk_scoring_enabled"])

    def test_disable_question_routing_forces_complex_path_for_ablation(self):
        df = pd.DataFrame({"Year": ["2020", "2021"], "Profit": [10, 30]})
        fake = FakePipelineLLM(semantic_score=0.01, rows=["2021"], cols=["Profit"])
        pipeline, _ = self._pipeline(fake)
        pipeline.disable_question_routing = True
        state = TQASessionState(
            question="What was the Profit in 2021?",
            df=df,
            table_schema=_build_table_schema(df),
        )

        result = pipeline.run(state)

        self.assertEqual(result.route_type, "COMPLEX")
        self.assertEqual(result.routing_context.get("ablation"), "question_routing_disabled")
        self.assertFalse(result.cost_metrics["question_routing_enabled"])

    def test_disable_table_compression_keeps_full_table_for_ablation(self):
        df = pd.DataFrame(
            {
                "Year": ["2020", "2021", "2022"],
                "Revenue": [100, 120, 150],
                "Profit": [10, 30, 40],
            }
        )
        fake = FakePipelineLLM(semantic_score=0.05, rows=["2021"], cols=["Revenue"])
        pipeline, _ = self._pipeline(fake)
        pipeline.disable_table_compression = True
        state = TQASessionState(
            question="What was the Revenue in 2021?",
            df=df,
            table_schema=_build_table_schema(df),
        )

        result = pipeline.run(state)

        self.assertEqual(result.compression_info["strategy"], "disabled_ablation_full_table")
        self.assertEqual(result.compression_info["compression_ratio"], 1.0)
        self.assertEqual(len(result.df), len(df))
        self.assertFalse(result.cost_metrics["table_compression_enabled"])

    def test_selective_high_risk_runs_candidate_judge(self):
        df = pd.DataFrame({"Year": ["2020", "2021"], "Profit": [10, 30]})
        fake = FakePipelineLLM(
            semantic_score=0.9,
            rows=["2020", "2021"],
            cols=["Year", "Profit"],
        )
        tracker = LLMCallTracker(fake)
        pipeline = TableQAPipeline(
            router=RouterAgent(tracker),
            planner=PlannerAgent(tracker),
            calculator=Calculator(),
            critic=CriticAgent(tracker),
            final_answer_agent=FinalAnswerAgent(tracker),
            enable_multi_view_validation=True,
            enable_selective_collaboration=True,
        )
        state = TQASessionState(
            question="What is the profit difference after comparing 2021 and 2020?",
            df=df,
            table_schema=_build_table_schema(df),
            dataset_profile="wtq",
        )

        result = pipeline.run(state)

        self.assertIsNotNone(result.agreement_decision)
        self.assertIsInstance(result.candidate_answers, list)

    def test_selective_high_risk_question_runs_strong_verifier(self):
        df = pd.DataFrame(
            {
                "Rank": [2, 1],
                "Competitor": ["Wrong Runner", "Esther Shahamorov"],
            }
        )
        fake = FakePipelineLLM(
            semantic_score=0.05,
            rows=[],
            cols=["Competitor"],
            direct_answer_output='{"answer":"Wrong Runner"}',
            thinking_output=(
                '{"answer":"Esther Shahamorov","confidence":0.92,'
                '"reasoning_summary":"top placing competitor is rank 1"}'
            ),
        )
        tracker = LLMCallTracker(fake)
        pipeline = TableQAPipeline(
            router=RouterAgent(tracker),
            planner=PlannerAgent(tracker),
            calculator=Calculator(),
            critic=CriticAgent(tracker),
            final_answer_agent=FinalAnswerAgent(tracker),
            enable_selective_collaboration=True,
        )
        state = TQASessionState(
            question="who was the top placing competitor?",
            df=df,
            table_schema=_build_table_schema(df),
            dataset_profile="wtq",
        )

        result = pipeline.run(state)

        self.assertEqual(result.final_value, "Esther Shahamorov")
        self.assertTrue(result.strong_verification_applied)
        self.assertIn("superlative_order", result.problem_tags)
        self.assertTrue(any("final high-risk verifier" in prompt for prompt in fake.prompts))

    def test_wtq_nonforced_strong_verifier_uses_direct_style_only(self):
        df = pd.DataFrame(
            {
                "Country": ["A", "B"],
                "Silver": [0, 1],
            }
        )
        fake = FakePipelineLLM(
            semantic_score=0.9,
            rows=["A", "B"],
            cols=["Country", "Silver"],
            thinking_output=(
                '{"answer":1,"confidence":0.92,'
                '"reasoning_summary":"one country has zero silver medals"}'
            ),
        )
        tracker = LLMCallTracker(fake)
        pipeline = TableQAPipeline(
            router=RouterAgent(tracker),
            planner=PlannerAgent(tracker),
            calculator=Calculator(),
            critic=CriticAgent(tracker),
            final_answer_agent=FinalAnswerAgent(tracker),
            enable_selective_collaboration=True,
        )
        state = TQASessionState(
            question="how many countries did not win any silver medals?",
            df=df,
            table_schema=_build_table_schema(df),
            dataset_profile="wtq",
        )

        result = pipeline.run(state)

        thinking_candidates = [
            candidate
            for candidate in result.candidate_answers
            if candidate.name.startswith("thinking_")
        ]
        self.assertEqual([candidate.name for candidate in thinking_candidates], ["thinking_direct"])

    def test_wtq_nonforced_verifier_does_not_overwrite_conflicting_valid_code(self):
        df = pd.DataFrame(
            {
                "Show": ["A", "B", "C", "D", "E", "F", "G", "H"],
                "Episodes": [2, 3, 4, 5, 6, 7, 8, 9],
            }
        )
        fake = FakePipelineLLM(
            semantic_score=0.9,
            rows=["A", "B", "C", "D", "E", "F", "G", "H"],
            cols=["Episodes"],
            planner_outputs=[
                "[PLAN]\n"
                "Step1: Count rows whose episode count is more than 1.\n"
                "[CODE]\n"
                "final_answer_value = int((df['Episodes'] > 1).sum())\n"
            ],
            thinking_output=(
                '{"answer":5,"confidence":0.95,'
                '"reasoning_summary":"incorrectly missed three qualifying shows"}'
            ),
        )
        tracker = LLMCallTracker(fake)
        pipeline = TableQAPipeline(
            router=RouterAgent(tracker),
            planner=PlannerAgent(tracker),
            calculator=Calculator(),
            critic=CriticAgent(tracker),
            final_answer_agent=FinalAnswerAgent(tracker),
            enable_selective_collaboration=True,
        )
        state = TQASessionState(
            question="in how many tv shows did the actor appear in more than 1 episode?",
            df=df,
            table_schema=_build_table_schema(df),
            dataset_profile="wtq",
        )

        result = pipeline.run(state)

        self.assertEqual(result.final_value, 8)
        self.assertTrue(result.strong_verification_applied)
        self.assertEqual(result.agreement_decision.reason, "valid_candidates_disagree")

    def test_wtq_answer_shape_mismatch_accepts_high_confidence_verifier(self):
        df = pd.DataFrame(
            {
                "Position": [3, 7, 12],
                "Driver": ["Alex Cole", "Mike Imrie", "Nina Page"],
                "Car": ["Ford", "Saab", "Lotus"],
            }
        )
        fake = FakePipelineLLM(
            semantic_score=0.9,
            rows=["Saab"],
            cols=["Position", "Driver", "Car"],
            planner_outputs=[
                "[PLAN]\n"
                "Step1: Find the only Saab row.\n"
                "Step2: Return the row position.\n"
                "[CODE]\n"
                "saab_row = df[df['Car'] == 'Saab']\n"
                "final_answer_value = saab_row['Position'].values[0]\n"
            ],
            thinking_output=(
                '{"answer":"Mike Imrie","confidence":0.95,'
                '"reasoning_summary":"the Saab row is driven by Mike Imrie"}'
            ),
        )
        tracker = LLMCallTracker(fake)
        pipeline = TableQAPipeline(
            router=RouterAgent(tracker),
            planner=PlannerAgent(tracker),
            calculator=Calculator(),
            critic=CriticAgent(tracker),
            final_answer_agent=FinalAnswerAgent(tracker),
            enable_selective_collaboration=True,
        )
        state = TQASessionState(
            question="who drove the only saab car?",
            df=df,
            table_schema=_build_table_schema(df),
            dataset_profile="wtq",
        )

        result = pipeline.run(state)

        self.assertEqual(result.final_value, "Mike Imrie")
        self.assertTrue(result.strong_verification_applied)
        self.assertEqual(
            result.agreement_decision.reason,
            "wtq_answer_shape_verifier_override",
        )

    def test_wtq_option_verifier_override_rejects_non_option_expansion(self):
        df = pd.DataFrame(
            {
                "Place": ["Sydney, Australia", "Coral Springs, Florida, USA"],
                "Date": ["2010", "2008"],
            }
        )
        state = TQASessionState(
            question="which was earlier, syndney, australia or coral springs, florida?",
            df=df,
            table_schema=_build_table_schema(df),
            dataset_profile="wtq",
        )
        selected = SimpleNamespace(
            name="code",
            normalized_answer="coral springs, florida",
            is_valid=True,
        )
        consensus = SimpleNamespace(
            name="thinking_direct",
            normalized_answer="Coral Springs, Florida, USA",
            is_valid=True,
            confidence=1.0,
        )

        self.assertFalse(
            TableQAPipeline._should_accept_wtq_verifier_override(
                state,
                selected,
                consensus,
            )
        )

    def test_wtq_negated_year_conflict_accepts_high_confidence_verifier(self):
        df = pd.DataFrame(
            {
                "Year": ["2012", "2013", "2014"],
                "Recipient": ["Herself", "U&I", "U&I"],
                "Result": ["Nominated", "Nominated", "Won"],
            }
        )
        state = TQASessionState(
            question='what year was the recipient not herself nor "heaven"?',
            df=df,
            table_schema=_build_table_schema(df),
            dataset_profile="wtq",
        )
        selected = SimpleNamespace(
            name="code",
            normalized_answer=2012.0,
            is_valid=True,
        )
        consensus = SimpleNamespace(
            name="thinking_direct",
            normalized_answer=2014.0,
            is_valid=True,
            confidence=0.95,
        )

        self.assertTrue(
            TableQAPipeline._should_accept_wtq_verifier_override(
                state,
                selected,
                consensus,
            )
        )

    def test_tabfact_single_cue_high_risk_label_does_not_auto_run_strong_verifier(self):
        df = pd.DataFrame({"team": ["A", "B"], "wins": [3, 2]})
        fake = FakePipelineLLM(semantic_score=0.9, rows=["A", "B"], cols=["team", "wins"])
        tracker = LLMCallTracker(fake)
        pipeline = TableQAPipeline(
            router=RouterAgent(tracker),
            planner=PlannerAgent(tracker),
            calculator=Calculator(),
            critic=CriticAgent(tracker),
            final_answer_agent=FinalAnswerAgent(tracker),
            enable_selective_collaboration=True,
        )
        state = TQASessionState(
            question="team A has more wins than team B",
            df=df,
            table_schema=_build_table_schema(df),
            answer_mode="true_false",
            answer_contract=infer_answer_contract(
                "team A has more wins than team B",
                answer_mode="true_false",
                reasoning_required=True,
            ),
            dataset_profile="tabfact",
        )
        state.problem_tags = ["comparison", "closed_choice"]
        state.risk_assessment = SimpleNamespace(level="high")

        should_verify, reason, forced = pipeline._should_apply_strong_verification(
            state,
            SimpleNamespace(requires_fallback=False, reason="agreement"),
        )

        self.assertFalse(should_verify)
        self.assertEqual(reason, "")
        self.assertFalse(forced)

    def test_tabfact_compound_high_risk_label_does_not_auto_run_strong_verifier(self):
        df = pd.DataFrame({"team": ["A", "B"], "wins": [3, 2], "year": [2019, 2020]})
        fake = FakePipelineLLM(semantic_score=0.9, rows=["A", "B"], cols=["team", "wins", "year"])
        tracker = LLMCallTracker(fake)
        pipeline = TableQAPipeline(
            router=RouterAgent(tracker),
            planner=PlannerAgent(tracker),
            calculator=Calculator(),
            critic=CriticAgent(tracker),
            final_answer_agent=FinalAnswerAgent(tracker),
            enable_selective_collaboration=True,
        )
        state = TQASessionState(
            question="after 2019, team A has more wins than team B",
            df=df,
            table_schema=_build_table_schema(df),
            answer_mode="true_false",
            answer_contract=infer_answer_contract(
                "after 2019, team A has more wins than team B",
                answer_mode="true_false",
                reasoning_required=True,
            ),
            dataset_profile="tabfact",
        )
        state.problem_tags = ["comparison", "temporal", "closed_choice"]
        state.risk_assessment = SimpleNamespace(level="high")

        should_verify, reason, forced = pipeline._should_apply_strong_verification(
            state,
            SimpleNamespace(requires_fallback=False, reason="agreement"),
        )

        self.assertFalse(should_verify)
        self.assertEqual(reason, "")
        self.assertFalse(forced)

    def test_tabfact_candidate_fallback_still_forces_strong_verifier(self):
        df = pd.DataFrame({"team": ["A", "B"], "wins": [3, 2]})
        fake = FakePipelineLLM(semantic_score=0.9, rows=["A", "B"], cols=["team", "wins"])
        tracker = LLMCallTracker(fake)
        pipeline = TableQAPipeline(
            router=RouterAgent(tracker),
            planner=PlannerAgent(tracker),
            calculator=Calculator(),
            critic=CriticAgent(tracker),
            final_answer_agent=FinalAnswerAgent(tracker),
            enable_selective_collaboration=True,
        )
        state = TQASessionState(
            question="team A has more wins than team B",
            df=df,
            table_schema=_build_table_schema(df),
            answer_mode="true_false",
            answer_contract=infer_answer_contract(
                "team A has more wins than team B",
                answer_mode="true_false",
                reasoning_required=True,
            ),
            dataset_profile="tabfact",
        )
        state.problem_tags = ["comparison", "closed_choice"]
        state.risk_assessment = SimpleNamespace(level="high")

        should_verify, reason, forced = pipeline._should_apply_strong_verification(
            state,
            SimpleNamespace(requires_fallback=True, reason="candidate_disagreement"),
        )

        self.assertTrue(should_verify)
        self.assertEqual(reason, "candidate_agreement:candidate_disagreement")
        self.assertTrue(forced)

    def test_deterministic_shortcut_is_not_overwritten_by_wrong_verifier(self):
        df = pd.DataFrame(
            {
                "Rank": [2, 1],
                "Competitor": ["Wrong Runner", "Esther Shahamorov"],
            }
        )
        fake = FakePipelineLLM(
            semantic_score=0.05,
            rows=[],
            cols=["Competitor"],
            thinking_output=(
                '{"answer":"Wrong Runner","confidence":0.98,'
                '"reasoning_summary":"incorrectly trusted a non-top row"}'
            ),
        )
        tracker = LLMCallTracker(fake)
        pipeline = TableQAPipeline(
            router=RouterAgent(tracker),
            planner=PlannerAgent(tracker),
            calculator=Calculator(),
            critic=CriticAgent(tracker),
            final_answer_agent=FinalAnswerAgent(tracker),
            enable_selective_collaboration=True,
        )
        state = TQASessionState(
            question="who was the top placing competitor?",
            df=df,
            table_schema=_build_table_schema(df),
            dataset_profile="wtq",
        )

        result = pipeline.run(state)

        self.assertEqual(result.final_value, "Esther Shahamorov")
        self.assertTrue(result.deterministic_shortcut_applied)
        self.assertEqual(result.agreement_decision.reason, "deterministic_shortcut_preserved")

    def test_legacy_mode_does_not_populate_selective_fields(self):
        df = pd.DataFrame({"Name": ["Alpha"], "Value": [7]})
        fake = FakePipelineLLM(semantic_score=0.05, rows=["Alpha"], cols=["Value"])
        pipeline, _ = self._pipeline(fake)
        state = TQASessionState(
            question="What is the value for Alpha?",
            df=df,
            table_schema=_build_table_schema(df),
            dataset_profile="wtq",
        )

        result = pipeline.run(state)

        self.assertIsNone(result.risk_assessment)
        self.assertIsNone(result.evidence_pack)

    def test_tabfact_same_row_pair_shortcut_handles_two_column_conditions(self):
        df = pd.DataFrame(
            {
                "home team": ["norwich city", "chelsea"],
                "score": ["1 - 2", "2 - 3"],
                "away team": ["bradford city", "crystal palace"],
            }
        )
        pipeline, _ = self._pipeline(FakePipelineLLM(0.8, [], []))
        question = (
            "chelsea be the home team when crystal palace be the away team and "
            "norwich city be the home team when bradford city be the away team"
        )
        state = TQASessionState(
            question=question,
            df=df,
            table_schema=_build_table_schema(df),
            answer_mode="true_false",
            answer_contract=infer_answer_contract(question, "true_false"),
            dataset_profile="tabfact",
        )

        self.assertTrue(pipeline._try_tabfact_semantic_shortcut(state))
        self.assertEqual(state.final_value, "true")

    def test_tabfact_numbered_same_team_shortcut_checks_combined_number_columns(self):
        df = pd.DataFrame(
            {
                "no6": ["celia paulo", "paulo telmo", "paulo"],
                "no9": ["telmo celia", "evicted (day 87)", "telmo"],
                "final": ["winner", "evicted", "housemate"],
            }
        )
        pipeline, _ = self._pipeline(FakePipelineLLM(0.8, [], []))
        question = "telmo and paulo play on the same team as no 6 and no 9"
        state = TQASessionState(
            question=question,
            df=df,
            table_schema=_build_table_schema(df),
            answer_mode="true_false",
            answer_contract=infer_answer_contract(question, "true_false"),
            dataset_profile="tabfact",
        )

        self.assertTrue(pipeline._try_tabfact_semantic_shortcut(state))
        self.assertEqual(state.final_value, "true")

    def test_tabfact_only_not_from_country_shortcut(self):
        df = pd.DataFrame(
            {
                "player": ["byron nelson", "jimmy thomson", "tommy armour"],
                "country": ["united states", "scotland united states", "scotland"],
            }
        )
        pipeline, _ = self._pipeline(FakePipelineLLM(0.8, [], []))
        question = "the only player who be not from the united state be from scotland"
        state = TQASessionState(
            question=question,
            df=df,
            table_schema=_build_table_schema(df),
            answer_mode="true_false",
            answer_contract=infer_answer_contract(question, "true_false"),
            dataset_profile="tabfact",
        )

        self.assertTrue(pipeline._try_tabfact_semantic_shortcut(state))
        self.assertEqual(state.final_value, "true")

    def test_tabfact_entity_numeric_year_value_shortcut(self):
        df = pd.DataFrame(
            {
                "district": ["paco", "santa mesa"],
                "population (2010 census)": ["70978", "99993"],
            }
        )
        pipeline, _ = self._pipeline(FakePipelineLLM(0.8, [], []))
        question = "the santa mesa district have a population of 99993 in 2010"
        state = TQASessionState(
            question=question,
            df=df,
            table_schema=_build_table_schema(df),
            answer_mode="true_false",
            answer_contract=infer_answer_contract(question, "true_false"),
            dataset_profile="tabfact",
        )

        self.assertTrue(pipeline._try_tabfact_semantic_shortcut(state))
        self.assertEqual(state.final_value, "true")

    def test_tabfact_column_value_count_and_minmax_diff_shortcuts(self):
        pipeline, _ = self._pipeline(FakePipelineLLM(0.8, [], []))
        count_df = pd.DataFrame(
            {
                "race name": [f"race {index}" for index in range(20)],
                "constructor": ["lotus - climax"] * 11 + ["cooper - climax"] * 9,
            }
        )
        count_question = "the constructor be lotus - climax for 11 of the 20 one race"
        count_state = TQASessionState(
            question=count_question,
            df=count_df,
            table_schema=_build_table_schema(count_df),
            answer_mode="true_false",
            answer_contract=infer_answer_contract(count_question, "true_false"),
            dataset_profile="tabfact",
        )

        self.assertTrue(pipeline._try_tabfact_semantic_shortcut(count_state))
        self.assertEqual(count_state.final_value, "true")

        diff_df = pd.DataFrame({"name": ["a", "b"], "avge": ["0.15", "0.48"]})
        diff_question = "the lowest average be 0.33 lower than the highest average"
        diff_state = TQASessionState(
            question=diff_question,
            df=diff_df,
            table_schema=_build_table_schema(diff_df),
            answer_mode="true_false",
            answer_contract=infer_answer_contract(diff_question, "true_false"),
            dataset_profile="tabfact",
        )

        self.assertTrue(pipeline._try_tabfact_semantic_shortcut(diff_state))
        self.assertEqual(diff_state.final_value, "true")

    def test_crt_e3_guard_shortcuts_cover_outlier_topk_and_constructor_percentage(self):
        pipeline, _ = self._pipeline(FakePipelineLLM(0.8, [], []))

        outlier_df = pd.DataFrame(
            {
                "event": ["a", "b", "c", "d", "e", "f", "g"],
                "stages": ["1 stage", "1 stage", "1 stage", "1 stage", "2 stages", "1 stage", "2 stages"],
                "acts": ["5 bands", "5 bands", "60 + djs", "6 bands", "12 bands", "9 bands", "13 bands"],
            }
        )
        outlier_question = (
            "Can we identify any outlier events based on the number of acts or number of stages "
            "compared to the other events in the table? Answer with only 'Yes' or 'No' that is "
            "most accurate and nothing else."
        )
        outlier_state = TQASessionState(
            question=outlier_question,
            df=outlier_df,
            table_schema=_build_table_schema(outlier_df),
            answer_mode="yes_no",
            answer_contract=infer_answer_contract(outlier_question, "yes_no"),
            dataset_profile="crt",
        )
        self.assertTrue(pipeline._try_crt_semantic_shortcut(outlier_state))
        self.assertEqual(outlier_state.final_value, "Yes")

        years_df = pd.DataFrame(
            {
                "rank": ["1", "2", "3", "4", "4", "6", "7", "8", "8", "10", "11"],
                "name": [
                    "silvio piola",
                    "francesco totti",
                    "gunnar nordahl",
                    "giuseppe meazza",
                    "jose altafini",
                    "roberto baggio",
                    "kurt hamrin",
                    "giuseppe signori",
                    "alessandro del piero",
                    "gabriel batistuta",
                    "antonio di natale",
                ],
                "years": [
                    "1929 - 1954",
                    "1992 -",
                    "1948 - 1958",
                    "1929 - 1947",
                    "1958 - 1976",
                    "1985 - 2004",
                    "1956 - 1971",
                    "1991 - 2004",
                    "1993 - 2012",
                    "1991 - 2003",
                    "2002 -",
                ],
            }
        )
        years_question = "What is the average number of years played by the top 10 players on this list?"
        years_state = TQASessionState(
            question=years_question,
            df=years_df,
            table_schema=_build_table_schema(years_df),
            dataset_profile="crt",
        )
        self.assertTrue(pipeline._try_crt_semantic_shortcut(years_state))
        self.assertEqual(years_state.final_value, 18)

        constructor_df = pd.DataFrame(
            {
                "driver": ["a", "b", "c", "d", "e", "f", "g", "h"],
                "constructor": [
                    "renault",
                    "renault",
                    "ferrari",
                    "ferrari",
                    "williams - honda",
                    "williams - honda",
                    "tyrrell - renault",
                    "tyrrell - renault",
                ],
                "time / retired": [
                    "transmission",
                    "transmission",
                    "transmission",
                    "+ 1 lap",
                    "transmission",
                    "+ 1 lap",
                    "+ 1 lap",
                    "not classified",
                ],
            }
        )
        constructor_question = (
            "Which constructor had the highest percentage of their drivers retire due to "
            "transmission problems?"
        )
        constructor_state = TQASessionState(
            question=constructor_question,
            df=constructor_df,
            table_schema=_build_table_schema(constructor_df),
            dataset_profile="crt",
        )
        self.assertTrue(pipeline._try_crt_semantic_shortcut(constructor_state))
        self.assertEqual(constructor_state.final_value, "renault")

    def test_crt_medal_ratio_probability_and_rounding_shortcuts(self):
        medals = pd.DataFrame(
            {
                "nation": ["united states (usa)", "great britain (gbr)", "belarus (blr)"],
                "gold": [3, 1, 1],
                "total": [4, 2, 2],
            }
        )
        ratio_question = (
            "What is the ratio of gold medals earned by the United States to "
            "the total medals earned by UK?"
        )
        self.assertEqual(
            TableQAPipeline._crt_medal_ratio_answer(ratio_question, medals),
            "3:2",
        )

        probability_question = (
            "What is the probability that a nation will earn at least two gold medals "
            "in the 2012 Summer Olympics?"
        )
        self.assertEqual(
            TableQAPipeline._crt_medal_probability_answer(probability_question, medals),
            "33.3%",
        )

        season_df = pd.DataFrame({"08 a pts": [27, 22], "09 c pts": [36, 22]})
        self.assertEqual(
            TableQAPipeline._crt_total_points_season_ratio_answer(
                "What is the ratio of total points earned in the 2008 A season to "
                "total points earned in the 2009 C season?",
                season_df,
            ),
            0.84,
        )

    def test_crt_average_threshold_penalty_and_win_loss_shortcuts(self):
        golfers = pd.DataFrame(
            {
                "weeks": [656, 331, 97, 61, 56, 50, 44, 39],
                "order": [9, 3, 4, 2, 15, 5, 7, 16],
            }
        )
        self.assertEqual(
            TableQAPipeline._crt_average_metric_for_threshold_answer(
                "What is the average order of world number one golfers who have "
                "spent more than 40 weeks at the top?",
                golfers,
            ),
            6.4,
        )

        uefa = pd.DataFrame(
            {
                "team 1": ["manchester city", "marseille"],
                "agg": ["2 - 2 (4 - 3 p )", "4 - 3"],
                "team 2": ["aalborg bk", "ajax"],
            }
        )
        self.assertEqual(
            TableQAPipeline._crt_penalty_score_answer(
                "Did any teams have an aggregate score of 4 - 3 with a penalty shootout?",
                uefa,
            ),
            "Yes",
        )
        self.assertEqual(
            TableQAPipeline._crt_penalty_score_answer(
                "What teams played in a match with a score of 4 - 3 and went to penalties?",
                uefa,
            ),
            "manchester city and aalborg bk",
        )

        doubles = pd.DataFrame(
            {
                "outcome": ["winner", "winner", "runner - up", "runner - up"],
                "partner": ["jaime fillol", "jaime fillol", "jaime fillol", "other"],
            }
        )
        self.assertEqual(
            TableQAPipeline._crt_partner_win_loss_ratio_answer(
                "What is the win-loss ratio when Patricio Cornejo plays with Jaime Fillol?",
                doubles,
            ),
            "2:1",
        )

    def test_crt_range_variation_margin_and_entity_suffix_normalization(self):
        years = pd.DataFrame({"year established": [1769, 1832, 1477, 1911]})
        self.assertEqual(
            TableQAPipeline._crt_year_variation_answer(
                "How much variation is there in the year established among the members?",
                years,
            ),
            434,
        )

        games = pd.DataFrame(
            {
                "visitor": ["atlanta", "new jersey", "philadelphia"],
                "score": ["2 - 3", "3 - 1", "2 - 4"],
                "home": ["new jersey", "edmonton", "new jersey"],
            }
        )
        self.assertEqual(
            TableQAPipeline._crt_named_team_largest_margin_answer(
                "What was the largest margin of victory for New Jersey Devils during the season?",
                games,
            ),
            2,
        )

        self.assertEqual(_strip_entity_metadata("netherlands (ned)"), "netherlands")
        self.assertEqual(
            _canonicalize_crt_scalar(
                "netherlands (ned)",
                "If a nation's score was equal to the sum of the number of gold and silver medals it won, which nation had the highest score?",
                pd.DataFrame({"nation": ["netherlands (ned)", "belgium (bel)"]}),
            ),
            "netherlands",
        )

    def test_crt_scalar_normalization_handles_difference_sign_and_country_codes(self):
        df = pd.DataFrame(
            {
                "year": [2001, 2002],
                "gold": ["yafei zhang ( chn )", "other"],
            }
        )

        self.assertEqual(
            _canonicalize_crt_scalar(
                -1.0,
                "What is the average score difference between the winner and runner-up?",
                pd.DataFrame({"score": ["268", "269"]}),
            ),
            1,
        )
        self.assertEqual(
            _canonicalize_crt_scalar(
                "chn",
                "What is the most common country of origin for medalists in the Double Trap competition?",
                df,
            ),
            "China",
        )

    def test_tabfact_rank_country_count_shortcut(self):
        df = pd.DataFrame(
            {
                "place": ["t9", "t9", "t9", "t9"],
                "player": ["a", "b", "c", "d"],
                "country": ["united states", "united states", "united states", "spain"],
            }
        )

        self.assertEqual(
            TableQAPipeline._tabfact_rank_country_count_answer(
                "3 of the people tie for ninth place be from the united state",
                df,
            ),
            "true",
        )

    def test_tabfact_country_majority_and_unique_count_shortcuts(self):
        df = pd.DataFrame(
            {
                "country": ["united states", "wales", "japan", "spain"],
                "to par": ["+ 1", "+ 2", "- 1", "+ 3"],
            }
        )

        self.assertEqual(
            TableQAPipeline._tabfact_unique_country_count_answer(
                "there be a total of 4 country represent by the player",
                df,
            ),
            "true",
        )
        self.assertEqual(
            TableQAPipeline._tabfact_majority_over_par_country_answer(
                "a majority of the people who score over par be from the united state",
                df,
            ),
            "false",
        )

    def test_tabfact_frequency_and_only_not_country_shortcuts(self):
        rounds = pd.DataFrame({"round": ["3", "4", "4", "5", "5"]})
        self.assertEqual(
            TableQAPipeline._tabfact_only_column_value_not_count_answer(
                "only round 3 be not list 2 time",
                rounds,
            ),
            "true",
        )

        countries = pd.DataFrame(
            {
                "player": ["a", "b", "c"],
                "nationality": ["united states", "canada", "slovakia"],
            }
        )
        self.assertEqual(
            TableQAPipeline._tabfact_only_not_from_countries_answer(
                "the only player not from the united state or canada be from norway",
                countries,
            ),
            "false",
        )

    def test_tabfact_source_and_attendance_shortcuts(self):
        sources = pd.DataFrame(
            {
                "player": ["a", "b"],
                "college / junior / club team (league)": ["new england jr coyotes (ejhl)", "centennial high school"],
            }
        )
        self.assertEqual(
            TableQAPipeline._tabfact_every_player_source_answer(
                "every player come from either a college program or a junior / club team",
                sources,
            ),
            "true",
        )

        games = pd.DataFrame(
            {
                "team": ["new orleans", "minnesota"],
                "location attendance": ["new orleans arena 17781", "target center 18478"],
            }
        )
        self.assertEqual(
            TableQAPipeline._tabfact_opponent_attendance_comparison_answer(
                "the game against minnesota have a higher attendance than the game against new orleans",
                games,
            ),
            "true",
        )

    def test_tabfact_extreme_difference_and_metric_order_shortcuts(self):
        scores = pd.DataFrame({"points": ["186.92", "112.28", "150.00"]})
        self.assertEqual(
            TableQAPipeline._tabfact_extreme_score_difference_answer(
                "there be a 74.64 point difference between the highest score (186.92) and the lowest score (112.28)",
                scores,
            ),
            "true",
        )

        tournaments = pd.DataFrame(
            {
                "tournament": ["us open", "the open championship", "pga championship"],
                "events": ["2", "7", "6"],
                "cuts made": ["1", "4", "4"],
            }
        )
        self.assertEqual(
            TableQAPipeline._tabfact_entity_metric_more_than_answer(
                "the pga championship have 3 more cut made than the us open",
                tournaments,
            ),
            "true",
        )
        self.assertEqual(
            TableQAPipeline._tabfact_second_highest_metric_entity_answer(
                "for brian watts , the open championship be the tournament with his second highest number of event",
                tournaments,
            ),
            "false",
        )

    def test_tabfact_max_year_record_and_calendar_shortcuts(self):
        episodes = pd.DataFrame(
            {
                "title": ["the early road", "the lady of the lake", "the finale"],
                "uk viewers (million)": ["5.7", "6.3", "6.1"],
            }
        )
        self.assertEqual(
            TableQAPipeline._tabfact_entity_max_metric_answer(
                "the lady of the lake episode have the most uk viewer",
                episodes,
            ),
            "true",
        )

        goals = pd.DataFrame({"date": ["1 may 1999", "2 may 2000", "3 june 2000", "4 may 2001"]})
        self.assertEqual(
            TableQAPipeline._tabfact_only_year_more_than_count_answer(
                "2000 be the only year rafael marquez score more than 1 goal in international competition",
                goals,
            ),
            "true",
        )

        schedule = pd.DataFrame(
            {
                "date": ["august 2", "august 3", "august 5"],
                "record": ["52 - 55", "55 - 55", "56 - 56"],
                "score": ["8 - 9", "6 - 4", "7 - 3"],
                "attendance": ["200", "300", "100"],
            }
        )
        self.assertEqual(
            TableQAPipeline._tabfact_record_equal_count_answer(
                "there be only 2 day during august 2005 milwaukee brewers season on which the 2005 milwaukee brewers season have a 50 / 50 win / loss record",
                schedule,
            ),
            "true",
        )
        self.assertEqual(
            TableQAPipeline._tabfact_month_no_game_days_answer(
                "there be only 28 day in august on which the 2005 milwaukee brewers season do not have to play a game",
                schedule,
            ),
            "true",
        )
        self.assertEqual(
            TableQAPipeline._tabfact_lowest_attendance_result_answer(
                "the 2005 milwaukee brewers season win the game which have the lowest attendance of the month",
                schedule,
            ),
            "true",
        )

    def test_tabfact_award_surface_rank_and_tenure_shortcuts(self):
        awards = pd.DataFrame(
            {
                "year": ["1995", "2002", "2005", "2009", "2006"],
                "beer name": ["good old boy", "good old boy", "good old boy", "good old boy", "maggs magnificent mild"],
                "competition": [
                    "festival",
                    "siba south east region beer competition",
                    "siba south east region beer competition",
                    "camra london and south east regional competition",
                    "siba national beer competition",
                ],
            }
        )
        self.assertEqual(
            TableQAPipeline._tabfact_beer_award_count_answer(
                "west berkshire brewery 's good old boy beer have 4 award between 1995 and 2009",
                awards,
            ),
            "true",
        )

        tennis = pd.DataFrame({"surface": ["hard (i)", "clay", "hard"]})
        self.assertEqual(
            TableQAPipeline._tabfact_surface_count_answer(
                "galina voskoboeva play a total of 2 game on a hard tennis court",
                tennis,
            ),
            "true",
        )

        ranking = pd.DataFrame(
            {
                "rank": ["1", "4", "6"],
                "name": ["winner", "norbert schramm", "stephan bril"],
                "nation": ["united states", "west germany", "west germany"],
            }
        )
        self.assertEqual(
            TableQAPipeline._tabfact_not_champion_loss_answer(
                "norbert schramm be not the female lose the skating championship",
                ranking,
            ),
            "false",
        )
        self.assertEqual(
            TableQAPipeline._tabfact_top_n_country_no_medal_answer(
                "west germany have 2 of the top 6 but do not have anyone win a medal",
                ranking,
            ),
            "true",
        )

        roster = pd.DataFrame(
            {
                "player": ["darryl dawkins", "paul dawkins", "james donaldson"],
                "years for jazz": ["1987 - 88", "1979 - 80", "1993 , 1994 - 95"],
            }
        )
        self.assertEqual(
            TableQAPipeline._tabfact_tenure_gap_answer(
                "paul dawkins play for the jazz 7 year before darryl dawkins",
                roster,
            ),
            "true",
        )
        self.assertEqual(
            TableQAPipeline._tabfact_stint_duration_answer(
                "james donaldson have 2 stint on the jazz 's utah jazz all - time roster , total 5 year in total",
                roster,
            ),
            "false",
        )

    def test_tabfact_race_count_and_consecutive_win_shortcuts(self):
        races = pd.DataFrame(
            {
                "date": ["24 may", "25 may", "26 may", "total"],
                "winner": ["ettore meini ( ita )", "ettore meini ( ita )", "gerard loncke ( bel )", "-"],
                "race leader": ["alfredo binda ( ita )", "alfredo binda ( ita )", "alfredo binda ( ita )", "km (mi)"],
            }
        )
        self.assertEqual(
            TableQAPipeline._tabfact_race_column_count_answer(
                "alfredo binda be the race leader for 3 race in the 1933 giro d'italia",
                races,
            ),
            "true",
        )
        self.assertEqual(
            TableQAPipeline._tabfact_consecutive_date_wins_answer(
                "ettore meini win 2 race in a row , on may 24 and 25th , during the 1933 giro d'italia",
                races,
            ),
            "true",
        )


if __name__ == "__main__":
    unittest.main()
