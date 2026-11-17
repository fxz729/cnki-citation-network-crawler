import unittest

from scrapers.journal_shards import select_journal_shard, split_journals


class JournalShardsTests(unittest.TestCase):
    def test_split_journals_uses_stable_round_robin_distribution(self):
        journals = [f"期刊{index}" for index in range(1, 11)]

        shards = split_journals(journals, 3)

        self.assertEqual(
            shards,
            [
                ["期刊1", "期刊4", "期刊7", "期刊10"],
                ["期刊2", "期刊5", "期刊8"],
                ["期刊3", "期刊6", "期刊9"],
            ],
        )

    def test_split_journals_evenly_divides_126_journals_between_three_shards(self):
        journals = [f"期刊{index}" for index in range(1, 127)]

        shards = split_journals(journals, 3)

        self.assertEqual([len(shard) for shard in shards], [42] * 3)
        assigned_journals = [journal for shard in shards for journal in shard]
        self.assertEqual(len(assigned_journals), 126)
        self.assertEqual(set(assigned_journals), set(journals))

    def test_split_journals_evenly_divides_126_journals_between_eight_shards(self):
        journals = [f"期刊{index}" for index in range(1, 127)]

        shards = split_journals(journals, 8)

        self.assertEqual([len(shard) for shard in shards], [16, 16, 16, 16, 16, 16, 15, 15])
        assigned_journals = [journal for shard in shards for journal in shard]
        self.assertEqual(len(assigned_journals), 126)
        self.assertEqual(set(assigned_journals), set(journals))

    def test_select_journal_shard_uses_stable_round_robin_distribution(self):
        journals = [f"期刊{index}" for index in range(1, 7)]

        selected = select_journal_shard(journals, shard_id=2, shard_count=3)

        self.assertEqual(selected, ["期刊2", "期刊5"])


    def test_select_journal_shard_rejects_invalid_shard_id(self):
        with self.assertRaisesRegex(ValueError, "shard_id must be between 1 and shard_count"):
            select_journal_shard(["期刊1"], shard_id=4, shard_count=3)

    def test_split_journals_rejects_invalid_shard_count(self):
        with self.assertRaisesRegex(ValueError, "shard_count must be positive"):
            split_journals(["期刊1"], 0)


if __name__ == "__main__":
    unittest.main()
