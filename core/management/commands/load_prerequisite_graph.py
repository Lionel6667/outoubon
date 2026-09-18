"""
Management command: Charge le graphe de prérequis depuis un fichier JSON vers la table TopicNode.
"""
import json
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand
from core.models import TopicNode


class Command(BaseCommand):
    help = "Peuple les TopicNodes et leurs liens de prérequis depuis database/prerequisites_bac.json"

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            type=str,
            default="database/prerequisites_bac.json",
            help="Chemin vers le fichier JSON des prérequis",
        )

    def handle(self, *args, **options):
        file_path = options.get("file", "database/prerequisites_bac.json")
        path = Path(settings.BASE_DIR) / file_path
        if not path.exists():
            self.stderr.write(f"Fichier introuvable: {path}")
            return

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.stdout.write(f"Chargement de {len(data)} nœuds de compétences...")

        # 1. Création des TopicNodes
        node_map = {}
        for topic_id, item in data.items():
            label = item.get("label", topic_id)
            subject = item.get("subject", "maths")
            serie = item.get("serie", "")
            node, _ = TopicNode.objects.get_or_create(
                topic_id=topic_id,
                defaults={"label": label, "subject": subject, "serie": serie},
            )
            node.label = label
            node.subject = subject
            node.serie = serie
            node.save()
            node_map[topic_id] = node

        # 2. Liaison des prérequis
        links_count = 0
        for topic_id, item in data.items():
            node = node_map[topic_id]
            prereqs = item.get("prerequisites", [])
            for p_id in prereqs:
                p_node = node_map.get(p_id)
                if not p_node:
                    p_node, _ = TopicNode.objects.get_or_create(
                        topic_id=p_id,
                        defaults={"label": p_id, "subject": node.subject},
                    )
                    node_map[p_id] = p_node
                node.prerequisites.add(p_node)
                links_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Succès : {len(node_map)} TopicNodes créés/mis à jour avec {links_count} relations de prérequis."
            )
        )
