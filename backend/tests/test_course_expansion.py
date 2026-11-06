"""Curriculum release, complete exports and teacher-only solutions."""

import json

import pytest
import test_identity
from test_assignments import add_student, grant
from test_identity import logged_in
from test_teaching import create_session

from cra import course
from cra.mission_access import catalog, mission_day

identities = test_identity.identities


def test_curriculum_references_and_existing_assignment_order():
    assert [d.day for d in course.days()][:4] == [1, 2, 3, 4]
    assert [m.id for m in catalog()][:3] == ["first-files", "first-contact", "web-trail"]
    assert mission_day("access-granted") == 4
    assert mission_day("web-trail-dns") == 3
    for day in course.days():
        assert (course.CONTENT / "teacher" / f"day-{day.day:02}.md").is_file()


def test_unknown_mission_and_missing_teacher_material_fail_closed(monkeypatch, tmp_path):
    content = json.loads((course.CONTENT / "day-01.json").read_text())
    monkeypatch.setattr(course, "CONTENT", tmp_path)
    path = tmp_path / "day-01.json"
    path.write_text(json.dumps({**content, "mission_id": "does-not-exist"}))
    with pytest.raises(RuntimeError, match="unknown mission"):
        course.days()
    path.write_text(json.dumps(content))
    with pytest.raises(RuntimeError, match="teacher notes"):
        course.days()


def test_individual_release_opens_material_only_for_recipient_and_exports_all_sections(identities):
    second_id = add_student(identities[0], identities[2])
    teacher, th = logged_in("teacher")
    student, _ = logged_in()
    second, _ = logged_in("secondstudent")
    create_session(teacher, th)
    assert [d["day"] for d in student.get("/api/v1/course/days").json()] == [1]
    assert grant(teacher, th, identities[2], mission="access-granted").status_code == 200
    assert [d["day"] for d in student.get("/api/v1/course/days").json()] == [1, 4]
    assert [d["day"] for d in second.get("/api/v1/course/days").json()] == [1]
    assert second.get("/api/v1/course/days/4/export").status_code == 403
    assert student.get("/api/v1/course/days/4/export").status_code == 200
    assert second_id != identities[2]
    for day in course.days():
        path = f"/api/v1/course/days/{day.day}"
        assert student.get(path + "/teacher-notes").status_code == 403
        assert teacher.get(path + "/teacher-notes").status_code == 200
        exported = teacher.get(path + "/export")
        assert exported.status_code == 200
        for text in [*day.schedule, *day.commands, *day.bonus, *day.reflection]:
            assert text in exported.text
