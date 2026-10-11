package dev.tappet.head

import java.io.File

/** Locates repo files from the JVM unit-test working directory (android/app) via relative paths. */
object TestFiles {
    fun repoFile(rel: String): File {
        val candidates = listOf("../../$rel", "../$rel", rel, "../../../$rel")
        return candidates.map { File(it) }.firstOrNull { it.exists() }
            ?: error("cannot find $rel from ${File(".").absolutePath}")
    }

    fun robotJson(): String = repoFile("config/robot.json").readText()
}
