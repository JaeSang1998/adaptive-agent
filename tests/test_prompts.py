"""prompt 조립 테스트."""

from adaptive_agent.llm.prompts import builder_code_messages


def test_builder_code_messages_renders_explicit_input_keys():
    """Builder 는 Planner 가 명시한 explicit input key 만 받는다."""
    messages = builder_code_messages(
        description="CSV를 분석한다",
        user_request="sales_data.csv를 분석해줘",
        input_data={
            "rows": [{"col1": 1, "col2": 2}],
        },
    )

    content = messages[1]["content"]
    assert "## 실행 시 전달될 input" in content
    assert '"rows"' in content
    assert '"col1"' in content
    # underscore-prefix 시스템 키가 input_data 에 섞여 들어가지 않음
    assert '"_data"' not in content
    assert '"_source_path"' not in content
    assert '"_source_tool"' not in content
    assert "원시 입력 데이터" not in content
