#!/usr/bin/env python3

import logging

import sqlalchemy as sa

import ws.utils
from ws.utils import value_or_none
from ws.parser_helpers.title import Title
from ws.db.selects import logevents

from . import Grabber

logger = logging.getLogger(__name__)

# TODO: allow syncing content independently from revisions
# TODO: are truncated results due to PHP cache reflected by changing the query-continuation parameter accordingly or do we actually lose some revisions?
class GrabberRevisions(Grabber):

    def __init__(self, api, db, with_content=False):
        super().__init__(api, db)
        self.with_content = with_content

        ins_text = sa.dialects.postgresql.insert(db.text)
        ins_revision = sa.dialects.postgresql.insert(db.revision)
        ins_archive = sa.dialects.postgresql.insert(db.archive)

        self.sql = {
            ("insert", "text"):
                ins_text.on_conflict_do_update(
                    constraint=db.text.primary_key,
                    set_={
                        "old_text":  ins_text.excluded.old_text,
                        "old_flags": ins_text.excluded.old_flags,
                    }),
            ("insert", "revision"):
                ins_revision.on_conflict_do_update(
                    constraint=db.revision.primary_key,
                    set_={
                        # this should be the only columns that may change in the table
                        "rev_deleted": ins_revision.excluded.rev_deleted,
                        # TODO: merging might change rev_page and rev_parent_id
                    }),
            ("insert", "archive"):
                ins_archive.on_conflict_do_update(
                    index_elements=[db.archive.c.ar_rev_id],
                    set_={
                        # this should be the only columns that may change in the table
                        "ar_deleted": ins_archive.excluded.ar_deleted,
                        # TODO: merging might change ar_page_id and ar_parent_id
                    }),
            # query for updating archive.ar_page_id
            ("update", "archive.ar_page_id"):
                db.archive.update() \
                    .where(sa.and_(db.archive.c.ar_namespace == sa.bindparam("b_namespace"),
                                   db.archive.c.ar_title == sa.bindparam("b_title")))
        }

        # build query to move data from the archive table into revision
        deleted_revisions = self.db.archive.delete() \
            .where(self.db.archive.c.ar_page_id == sa.bindparam("b_page_id")) \
            .returning(*self.db.archive.c._all_columns) \
            .cte("deleted_revisions")
        columns = [
                deleted_revisions.c.ar_rev_id,
                deleted_revisions.c.ar_page_id,
                deleted_revisions.c.ar_text_id,
                deleted_revisions.c.ar_comment,
                deleted_revisions.c.ar_user,
                deleted_revisions.c.ar_user_text,
                deleted_revisions.c.ar_timestamp,
                deleted_revisions.c.ar_minor_edit,
                deleted_revisions.c.ar_deleted,
                deleted_revisions.c.ar_len,
                deleted_revisions.c.ar_parent_id,
                deleted_revisions.c.ar_sha1,
                deleted_revisions.c.ar_content_model,
                deleted_revisions.c.ar_content_format,
            ]
        insert = self.db.revision.insert().from_select(
            self.db.revision.c._all_columns,
            sa.select(columns).select_from(deleted_revisions)
        )
        self.sql["move", "revision"] = insert

        props = "ids|timestamp|flags|user|userid|comment|size|sha1|contentmodel"
        if self.with_content is True:
            props += "|content"

        # TODO: tags
        self.arv_params = {
            "list": "allrevisions",
            "arvprop": props,
            "arvlimit": "max",
        }

        # TODO: tags
        self.adr_params = {
            "list": "alldeletedrevisions",
            "adrprop": props,
            "adrlimit": "max",
        }

        # TODO: check the permission to view deleted revisions
#        if "patrol" in self.api.user.rights:
#            self.rc_params["rcprop"] += "|patrolled"
#        else:
#            logger.warning("You need the 'patrol' right to request the patrolled flag. "
#                           "Skipping it, but the sync will be incomplete.")

    # TODO: text.old_id is auto-increment, but revision.rev_text_id has to be set accordingly. SQL should be able to do it automatically.
    def _get_text_id(self, conn):
        result = conn.execute(sqlalchemy.select( [sa.sql.func.max(self.db.text.c.old_id)] ))
        value = result.fetchone()[0]
        if value is None:
            value = 0
        while True:
            value += 1
            yield value

    def gen_text(self, rev, text_id):
        db_entry = {
            "old_id": text_id,
            "old_text": rev["*"],
            "old_flags": "utf-8",
        }
        yield self.sql["insert", "text"], db_entry

    def gen_revisions(self, page):
        for rev in page["revisions"]:
            db_entry = {
                "rev_id": rev["revid"],
                "rev_page": value_or_none(page.get("pageid")),
                "rev_comment": rev["comment"],
                "rev_user": rev["userid"],
                "rev_user_text": rev["user"],
                "rev_timestamp": rev["timestamp"],
                "rev_minor_edit": "minor" in rev,
                # TODO: rev_deleted
                "rev_len": rev["size"],
                # TODO: read on page history merging
                "rev_parent_id": rev.get("parentid"),
                "rev_sha1": rev["sha1"],
                "rev_content_model": rev["contentmodel"],
            }

            if self.with_content is True:
                text_id = next(text_id_gen)
                db_entry["rev_text_id"] = text_id
                yield from self.gen_text(rev, text_id)

            yield self.sql["insert", "revision"], db_entry

    def gen_deletedrevisions(self, page):
        title = Title(self.api, page["title"])
        for rev in page["revisions"]:
            db_entry = {
                "ar_namespace": page["ns"],
                "ar_title": title.dbtitle(page["ns"]),
                "ar_rev_id": rev["revid"],
                # NOTE: list=alldeletedrevisions always returns 0
                "ar_page_id": value_or_none(page.get("pageid")),
                "ar_comment": rev["comment"],
                "ar_user": rev["userid"],
                "ar_user_text": rev["user"],
                "ar_timestamp": rev["timestamp"],
                "ar_minor_edit": "minor" in rev,
                # TODO: ar_deleted
                "ar_len": rev["size"],
                # ar_parent_id is not visible through API
                "ar_sha1": rev["sha1"],
                "ar_content_model": rev["contentmodel"],
            }

            if self.with_content is True:
                text_id = next(text_id_gen)
                db_entry["rev_text_id"] = text_id
                yield from self.gen_text(rev, text_id)

            yield self.sql["insert", "archive"], db_entry

    # TODO: write custom insert and update methods, use discontinued API queries and wrap each chunk in a separate transaction
    # TODO: generalize the above even for logging table

    def gen_insert(self):
        for page in self.api.list(self.arv_params):
            yield from self.gen_revisions(page)
        for page in self.api.list(self.adr_params):
            yield from self.gen_deletedrevisions(page)

    def gen_update(self, since):
        # TODO: make sure that the updates from the API don't create a duplicate row with a new ID in the text table

        arv_params = self.arv_params.copy()
        arv_params["arvdir"] = "newer"
        arv_params["arvstart"] = since
        for page in self.api.list(arv_params):
            yield from self.gen_revisions(page)

        deleted_pages = set()
        undeleted_pages = set()

        le_params = {
            "type": "delete",
            "prop": {"type", "details", "title"},
            "dir": "newer",
            "start": since,
        }
        for le in logevents.list(self.db, le_params):
            if le["type"] == "delete":
                if le["action"] == "delete":
                    deleted_pages.add(le["title"])
                elif le["action"] == "restore":
                    undeleted_pages.add((le["title"], le["pageid"]))

        # symmetric difference - simplify delete after undelete and undelete after delete
        deleted_pages = set(page for page in deleted_pages if page not in undeleted_pages)
        undeleted_pages = set(t for t in undeleted_pages if t[0] not in deleted_pages)

        # handle undelete - move the rows from archive to revision (deletes are handled in the page grabber)
        for _title, pageid in undeleted_pages:
            # ar_page_id is apparently not visible via list=alldeletedrevisions,
            # so we have to update it here first
            title = Title(self.api, _title)
            ns = title.namespacenumber
            dbtitle = title.dbtitle(ns),
            yield self.sql["update", "archive.ar_page_id"], {"b_namespace": ns, "b_title": dbtitle, "ar_page_id": pageid}
            # move the updated rows from archive to revision
            yield self.sql["move", "revision"], {"b_page_id": pageid}

        # MW defect: it is not possible to use list=alldeletedrevisions to get all new
        # deleted revisions the same way as normal revisions, because adrstart can be
        # used only along with adruser (archive.ar_timestamp is not indexed separately).
        #
        # To work around this, we realize that new deleted revisions can appear only by
        # deleting an existing page, which creates an entry in the logging table. We
        # still need to query the API with prop=deletedrevisions to get even the
        # revisions that were created and deleted since the last sync.
        for chunk in ws.utils.iter_chunks(deleted_pages, self.api.max_ids_per_query):
            params = {
                "action": "query",
                "titles": "|".join(chunk),
                "prop": "deletedrevisions",
                "drvprop": self.adr_params["adrprop"],
                "drvlimit": "max",
                "drvstart": since,
                "drvdir": "newer",
            }
            result = self.api.call_api(params, expand_result=False)
            # TODO: handle 'drvcontinue'
            if "drvcontinue" in result:
                raise NotImplementedError("Handling of the 'drvcontinue' parameter is not implemented.")
            for page in result["query"]["pages"].values():
                if "deletedrevisions" in page:
                    # update the dict for gen_deletedrevisions to understand
                    page["revisions"] = page.pop("deletedrevisions")
                    yield from self.gen_deletedrevisions(page)

        # TODO: update rev_deleted and ar_deleted
        # TODO: handle merge and unmerge
