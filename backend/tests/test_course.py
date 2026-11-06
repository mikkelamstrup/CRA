from concurrent.futures import ThreadPoolExecutor

import test_identity
from test_identity import logged_in

from cra.contracts import missions
from cra.course import days

identities = test_identity.identities


def test_course_releases_reference_real_missions():
    catalog = {(m.id, m.version) for m in missions()}
    for day in days():
        assert (day.mission_id, day.mission_version) in catalog
        assert len(day.lessons) >= 4 and len(day.goals) >= 3
        assert "CRA{" not in day.model_dump_json()


def test_private_notebook_conflict_retry_export_and_teacher_boundary(identities):
    client, headers = logged_in()
    other, oh = logged_in("outsider")
    teacher, _ = logged_in("teacher")
    path = "/api/v1/course/days/1/notebook"
    assert client.get(path).json()["revision"] == 0
    assert client.get("/api/v1/course/days/1/teacher-notes").status_code == 403
    assert teacher.get("/api/v1/course/days/1/teacher-notes").status_code == 200
    body = {
        "revision": 0,
        "body": "<script>synthetic observation</script>",
        "reflection": "Source, observation, uncertainty",
        "theory_read": True,
    }
    saved = client.put(path, headers=headers, json=body)
    assert saved.status_code == 200 and saved.json()["revision"] == 1
    assert client.put(path, headers=headers, json=body).json() == saved.json()
    assert (
        client.put(path, headers=headers, json={**body, "body": "stale change"}).status_code == 409
    )
    assert other.get(path).json()["body"] == ""
    assert (
        client.get("/api/v1/course/days/1/export").headers["content-type"].startswith("text/plain")
    )
    assert (
        client.put(path, headers=headers, json={**body, "user_id": str(identities[4])}).status_code
        == 422
    )
    assert client.put(path, json=body).status_code == 403
    assert client.put(path, headers=headers, json={**body, "body": "x" * 16001}).status_code == 422
    assert client.get("/api/v1/course/days/9/notebook").status_code == 404
    assert other.put(path, headers=oh, json={**body, "body": "other notes"}).status_code == 200
    assert client.get(path).json()["body"] == body["body"]


def test_concurrent_notebook_edits_cannot_silently_overwrite(identities):
    client, headers = logged_in()
    path = "/api/v1/course/days/1/notebook"

    def edit(index):
        return client.put(
            path,
            headers=headers,
            json={
                "revision": 0,
                "body": f"Independent edit {index}",
                "reflection": "",
                "theory_read": False,
            },
        ).status_code

    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = list(pool.map(edit, range(4)))
    assert sorted(statuses) == [200, 409, 409, 409]
